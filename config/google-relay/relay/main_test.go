package main

import (
	"bufio"
	"bytes"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/binary"
	"encoding/json"
	"encoding/pem"
	"io"
	"math/big"
	"net"
	"net/http"
	"net/http/httptest"
	"net/netip"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

const (
	testHost   = "hass-google.example.com"
	testClient = "https://oauth-redirect.googleusercontent.com/r/test-project"
	testBearer = "Bearer eyJhbGciOiJIUzI1NiJ9.eyJpc3MiOiJ4In0.c2lnbmF0dXJlc2lnbmF0dXJl"
)

// upstream records what reaches "Home Assistant".
type upstream struct {
	srv  *httptest.Server
	last *http.Request
	body []byte
	hits int
}

func newUpstream(t *testing.T) *upstream {
	u := &upstream{}
	u.srv = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		u.hits++
		u.last = r
		u.body, _ = io.ReadAll(r.Body)
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("Set-Cookie", "leak=1")
		w.Write([]byte(`{"ok":true}`))
	}))
	t.Cleanup(u.srv.Close)
	return u
}

func newTestRelay(u *upstream, mode string, ranges ...string) *relay {
	rs := &rangeSet{}
	for _, r := range ranges {
		rs.prefixes = append(rs.prefixes, netip.MustParsePrefix(r))
	}
	cfg := config{Upstream: u.srv.URL, Host: testHost, ClientID: testClient, RangeMode: mode}
	return newRelay(cfg, rs, u.srv.Client())
}

func post(path, ctype, body string) *http.Request {
	r := httptest.NewRequest(http.MethodPost, path, strings.NewReader(body))
	r.Host = testHost
	r.RemoteAddr = "203.0.113.7:4444"
	if ctype != "" {
		r.Header.Set("Content-Type", ctype)
	}
	return r
}

const syncBody = `{"requestId":"abc","inputs":[{"intent":"action.devices.SYNC"}]}`

func tokenForm(extra map[string]string) string {
	v := url.Values{"grant_type": {"refresh_token"}, "refresh_token": {"rt"}, "client_id": {testClient}, "client_secret": {"s"}}
	for k, x := range extra {
		if x == "" {
			v.Del(k)
		} else {
			v.Set(k, x)
		}
	}
	return v.Encode()
}

func TestFulfillmentIsRebuilt(t *testing.T) {
	u := newUpstream(t)
	rl := newTestRelay(u, "log")
	req := post(fulfillmentPath, "application/json; charset=utf-8",
		`{"requestId":"abc","inputs":[{"intent":"action.devices.EXECUTE","payload":{"commands":[]}}],"requestId":"abc"}`)
	req.Header.Set("Authorization", testBearer)
	req.Header.Set("Cookie", "session=steal")
	req.Header.Set("X-Forwarded-For", "10.0.0.1")
	req.Header.Set("X-Ha-Access", "legacy")
	w := httptest.NewRecorder()
	rl.ServeHTTP(w, req)
	if w.Code != 200 || u.hits != 1 {
		t.Fatalf("status %d, upstream hits %d: %s", w.Code, u.hits, w.Body)
	}
	h := u.last.Header
	if h.Get("Authorization") != testBearer || h.Get("Cookie") != "" || h.Get("X-Ha-Access") != "" {
		t.Fatalf("headers not filtered: %v", h)
	}
	if got := h.Values("X-Forwarded-For"); len(got) != 1 || got[0] != "203.0.113.7" {
		t.Fatalf("X-Forwarded-For %v", got)
	}
	var m map[string]any
	if err := json.Unmarshal(u.body, &m); err != nil || m["requestId"] != "abc" {
		t.Fatalf("body %s", u.body)
	}
	if w.Header().Get("Set-Cookie") != "" {
		t.Fatal("upstream headers leaked to the client")
	}
}

func TestFulfillmentRefusals(t *testing.T) {
	cases := map[string]struct {
		ctype, auth, body string
		want              int
	}{
		"no bearer":     {"application/json", "", syncBody, 401},
		"basic auth":    {"application/json", "Basic dXNlcjpwYXNz", syncBody, 401},
		"short bearer":  {"application/json", "Bearer x", syncBody, 401},
		"form":          {"application/x-www-form-urlencoded", testBearer, syncBody, 415},
		"array":         {"application/json", testBearer, `[1]`, 400},
		"no inputs":     {"application/json", testBearer, `{"requestId":"a"}`, 400},
		"bad intent":    {"application/json", testBearer, `{"requestId":"a","inputs":[{"intent":"x"}]}`, 400},
		"trailing":      {"application/json", testBearer, syncBody + `{}`, 400},
		"not json":      {"application/json", testBearer, `{"requestId":`, 400},
		"no request id": {"application/json", testBearer, `{"inputs":[{"intent":"action.devices.SYNC"}]}`, 400},
	}
	for name, c := range cases {
		t.Run(name, func(t *testing.T) {
			u := newUpstream(t)
			req := post(fulfillmentPath, c.ctype, c.body)
			if c.auth != "" {
				req.Header.Set("Authorization", c.auth)
			}
			w := httptest.NewRecorder()
			newTestRelay(u, "log").ServeHTTP(w, req)
			if w.Code != c.want || u.hits != 0 {
				t.Fatalf("status %d (want %d), upstream hits %d", w.Code, c.want, u.hits)
			}
		})
	}
}

func TestTokenKeepsOnlyWhatHomeAssistantNeeds(t *testing.T) {
	u := newUpstream(t)
	w := httptest.NewRecorder()
	newTestRelay(u, "log").ServeHTTP(w, post(tokenPath, "application/x-www-form-urlencoded", tokenForm(nil)))
	if w.Code != 200 || u.hits != 1 {
		t.Fatalf("status %d hits %d", w.Code, u.hits)
	}
	got, _ := url.ParseQuery(string(u.body))
	want := url.Values{"grant_type": {"refresh_token"}, "refresh_token": {"rt"}, "client_id": {testClient}}
	if got.Encode() != want.Encode() {
		t.Fatalf("forwarded %v", got)
	}
	if w.Header().Get("Cache-Control") != "no-store" {
		t.Fatal("token response cacheable")
	}
}

func TestTokenRefusals(t *testing.T) {
	cases := map[string]string{
		"other client":   tokenForm(map[string]string{"client_id": "https://evil.example/"}),
		"no client":      tokenForm(map[string]string{"client_id": ""}),
		"password grant": tokenForm(map[string]string{"grant_type": "password"}),
		"revoke":         tokenForm(map[string]string{"action": "revoke"}),
		"no code":        tokenForm(map[string]string{"grant_type": "authorization_code", "refresh_token": ""}),
		"duplicate":      tokenForm(nil) + "&client_id=" + url.QueryEscape(testClient),
		"bad encoding":   "grant_type=%zz",
	}
	for name, body := range cases {
		t.Run(name, func(t *testing.T) {
			u := newUpstream(t)
			w := httptest.NewRecorder()
			newTestRelay(u, "log").ServeHTTP(w, post(tokenPath, "application/x-www-form-urlencoded", body))
			if w.Code != 400 || u.hits != 0 {
				t.Fatalf("status %d, upstream hits %d", w.Code, u.hits)
			}
		})
	}
	u := newUpstream(t)
	req := post(tokenPath, "application/x-www-form-urlencoded", tokenForm(nil))
	req.Header.Set("Authorization", "Basic Zm9vOmJhcg==")
	w := httptest.NewRecorder()
	newTestRelay(u, "log").ServeHTTP(w, req)
	if w.Code != 400 || u.hits != 0 {
		t.Fatalf("basic auth on token: %d", w.Code)
	}
}

func TestEverythingElseIsRefused(t *testing.T) {
	u := newUpstream(t)
	rl := newTestRelay(u, "log")
	for _, c := range []struct {
		method, target string
		want           int
	}{
		{"GET", "/", 404},
		{"GET", "/auth/authorize", 404},
		{"POST", "/auth/login_flow", 404},
		{"GET", "/api/", 404},
		{"POST", "/api/services/lock/unlock", 404},
		{"GET", "/api/websocket", 404},
		{"POST", "/api/google_assistant/", 404},
		{"POST", "/API/google_assistant", 404},
		{"POST", "/api//google_assistant", 404},
		{"POST", "/api/google_assistant?x=1", 404},
		{"POST", "/api/%67oogle_assistant", 404},
		{"POST", "/api/./google_assistant", 404},
		{"POST", "https://" + testHost + "/api/google_assistant", 404},
		{"GET", "/api/google_assistant", 405},
		{"GET", "/auth/token", 405},
	} {
		req := httptest.NewRequest(c.method, c.target, nil)
		req.Host = testHost
		req.RemoteAddr = "203.0.113.7:4444"
		req.Header.Set("Authorization", testBearer)
		w := httptest.NewRecorder()
		rl.ServeHTTP(w, req)
		if w.Code != c.want {
			t.Errorf("%s %s: %d, want %d", c.method, c.target, w.Code, c.want)
		}
	}
	other := post(fulfillmentPath, "application/json", syncBody)
	other.Host = "hass.example.com"
	w := httptest.NewRecorder()
	rl.ServeHTTP(w, other)
	if w.Code != 404 {
		t.Errorf("other host: %d", w.Code)
	}
	big := post(fulfillmentPath, "application/json", `{"requestId":"a","inputs":[{"intent":"action.devices.SYNC"}],"pad":"`+strings.Repeat("x", maxRequest)+`"}`)
	big.Header.Set("Authorization", testBearer)
	w = httptest.NewRecorder()
	rl.ServeHTTP(w, big)
	if w.Code != 413 {
		t.Errorf("oversized: %d", w.Code)
	}
	if u.hits != 0 {
		t.Fatalf("%d refused requests reached Home Assistant", u.hits)
	}
}

func TestRateLimitPerAddress(t *testing.T) {
	u := newUpstream(t)
	rl := newTestRelay(u, "log")
	limited := 0
	for range 40 {
		w := httptest.NewRecorder()
		rl.ServeHTTP(w, post(tokenPath, "application/x-www-form-urlencoded", tokenForm(nil)))
		if w.Code == 429 {
			limited++
		}
	}
	if limited == 0 || u.hits > 6 {
		t.Fatalf("token endpoint not limited: %d upstream hits", u.hits)
	}
	other := post(tokenPath, "application/x-www-form-urlencoded", tokenForm(nil))
	other.RemoteAddr = "198.51.100.9:1"
	w := httptest.NewRecorder()
	rl.ServeHTTP(w, other)
	if w.Code != 200 {
		t.Fatalf("another address was limited too: %d", w.Code)
	}
}

func TestEnforcedRanges(t *testing.T) {
	u := newUpstream(t)
	rl := newTestRelay(u, "enforce", "198.51.100.0/24")
	w := httptest.NewRecorder()
	rl.ServeHTTP(w, post(tokenPath, "application/x-www-form-urlencoded", tokenForm(nil)))
	if w.Code != 403 || u.hits != 0 {
		t.Fatalf("outside the ranges: %d", w.Code)
	}
	in := post(tokenPath, "application/x-www-form-urlencoded", tokenForm(nil))
	in.RemoteAddr = "198.51.100.9:1"
	w = httptest.NewRecorder()
	rl.ServeHTTP(w, in)
	if w.Code != 200 {
		t.Fatalf("inside the ranges: %d", w.Code)
	}
}

func proxyHeader(src netip.AddrPort) []byte {
	var b bytes.Buffer
	b.Write(proxyV2Sig)
	if src.Addr().Is4() {
		b.Write([]byte{0x21, 0x11, 0, 12})
		a := src.Addr().As4()
		b.Write(a[:])
		b.Write([]byte{10, 0, 0, 1})
	} else {
		b.Write([]byte{0x21, 0x21, 0, 36})
		a := src.Addr().As16()
		b.Write(a[:])
		b.Write(make([]byte, 16))
	}
	binary.Write(&b, binary.BigEndian, src.Port())
	binary.Write(&b, binary.BigEndian, uint16(443))
	return b.Bytes()
}

func TestProxyHeader(t *testing.T) {
	for _, want := range []string{"203.0.113.7:4444", "[2001:db8::1]:55"} {
		ap := netip.MustParseAddrPort(want)
		got, err := readProxyV2(bufio.NewReader(bytes.NewReader(append(proxyHeader(ap), "rest"...))))
		if err != nil || got != ap {
			t.Fatalf("%s: got %v %v", want, got, err)
		}
	}
	local := append(append([]byte{}, proxyV2Sig...), 0x20, 0x00, 0, 0)
	bad := map[string][]byte{
		"v1":    []byte("PROXY TCP4 1.2.3.4 5.6.7.8 1 2\r\n"),
		"tls":   {0x16, 0x03, 0x01, 0x00, 0xa5, 0x01, 0, 0, 0xa1, 0x03, 0x03, 0, 0, 0, 0, 0},
		"local": local,
		"udp":   append(append([]byte{}, proxyV2Sig...), 0x21, 0x12, 0, 12, 1, 2, 3, 4, 5, 6, 7, 8, 0, 1, 0, 2),
		"short": proxyHeader(netip.MustParseAddrPort("1.2.3.4:5"))[:20],
		"huge":  append(append([]byte{}, proxyV2Sig...), 0x21, 0x11, 0xff, 0xff),
	}
	for name, b := range bad {
		if _, err := readProxyV2(bufio.NewReader(bytes.NewReader(b))); err == nil {
			t.Errorf("%s accepted", name)
		}
	}
}

// TestEndToEnd runs the real listener stack: PROXY header, then TLS for the right name only, then a request.
func TestEndToEnd(t *testing.T) {
	u := newUpstream(t)
	dir := t.TempDir()
	certFile, keyFile, pool := selfSigned(t, dir)
	certs := &certLoader{cert: certFile, key: keyFile}
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	tlsCfg := &tls.Config{MinVersion: tls.VersionTLS12, NextProtos: []string{"http/1.1"},
		GetCertificate: func(h *tls.ClientHelloInfo) (*tls.Certificate, error) {
			if h.ServerName != testHost {
				return nil, io.EOF
			}
			return certs.get()
		}}
	srv := &http.Server{Handler: newTestRelay(u, "log"), ReadHeaderTimeout: time.Second,
		TLSNextProto: map[string]func(*http.Server, *tls.Conn, http.Handler){}}
	go srv.Serve(tls.NewListener(&proxyListener{Listener: newLimitListener(ln, 4), timeout: time.Second}, tlsCfg))
	t.Cleanup(func() { srv.Close() })

	dial := func(sni string, header bool) (*tls.Conn, error) {
		c, err := net.Dial("tcp", ln.Addr().String())
		if err != nil {
			return nil, err
		}
		if header {
			c.Write(proxyHeader(netip.MustParseAddrPort("192.0.2.33:1234")))
		}
		tc := tls.Client(c, &tls.Config{ServerName: sni, RootCAs: pool})
		tc.SetDeadline(time.Now().Add(3 * time.Second))
		return tc, tc.Handshake()
	}
	if _, err := dial(testHost, false); err == nil {
		t.Fatal("connection without a PROXY header was served")
	}
	if _, err := dial("hass.example.com", true); err == nil {
		t.Fatal("handshake for another name succeeded")
	}
	tc, err := dial(testHost, true)
	if err != nil {
		t.Fatal(err)
	}
	if tc.ConnectionState().NegotiatedProtocol == "h2" {
		t.Fatal("HTTP/2 negotiated")
	}
	req := post(fulfillmentPath, "application/json", syncBody)
	req.Header.Set("Authorization", testBearer)
	req.RequestURI = ""
	if err := req.Write(tc); err != nil {
		t.Fatal(err)
	}
	resp, err := http.ReadResponse(bufio.NewReader(tc), req)
	if err != nil {
		t.Fatal(err)
	}
	if resp.StatusCode != 200 || u.last.Header.Get("X-Forwarded-For") != "192.0.2.33" {
		t.Fatalf("status %d, forwarded for %q", resp.StatusCode, u.last.Header.Get("X-Forwarded-For"))
	}
}

func selfSigned(t *testing.T, dir string) (string, string, *x509.CertPool) {
	key, _ := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	tmpl := &x509.Certificate{SerialNumber: big.NewInt(1), Subject: pkix.Name{CommonName: testHost},
		DNSNames: []string{testHost}, NotBefore: time.Now().Add(-time.Hour), NotAfter: time.Now().Add(time.Hour),
		KeyUsage: x509.KeyUsageDigitalSignature, ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth}}
	der, err := x509.CreateCertificate(rand.Reader, tmpl, tmpl, &key.PublicKey, key)
	if err != nil {
		t.Fatal(err)
	}
	kder, _ := x509.MarshalECPrivateKey(key)
	certFile, keyFile := filepath.Join(dir, "c.crt"), filepath.Join(dir, "c.key")
	os.WriteFile(certFile, pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der}), 0o600)
	os.WriteFile(keyFile, pem.EncodeToMemory(&pem.Block{Type: "EC PRIVATE KEY", Bytes: kder}), 0o600)
	leaf, _ := x509.ParseCertificate(der)
	pool := x509.NewCertPool()
	pool.AddCert(leaf)
	return certFile, keyFile, pool
}
