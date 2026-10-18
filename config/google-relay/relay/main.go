// Command relay is the only way in to Home Assistant from the internet, and it lets Google's smart home servers
// do exactly two things: send a fulfillment request (POST /api/google_assistant) and exchange or refresh an OAuth
// token (POST /auth/token). Everything else is refused here, before Home Assistant sees it.
//
// Its connections come from the google-tunnel container: the schlift.com VM passes TLS for the relay's hostname
// through unopened (Caddy layer4, by SNI), behind a PROXY v2 header with the client's address, down an SSH
// tunnel that this house dials out. TLS ends here, so the VM never sees a token.
//
// Requests are not passed through, they are rebuilt: a fulfillment body is parsed and re-encoded, a token request
// keeps only the fields Home Assistant needs, and only a few headers survive. A parser disagreement between this
// and Home Assistant can then only ever be about a request that is already well-formed.
package main

import (
	"bufio"
	"bytes"
	"crypto/tls"
	"encoding/binary"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net"
	"net/http"
	"net/netip"
	"net/url"
	"os"
	"regexp"
	"strings"
	"sync"
	"time"
)

type config struct {
	Listen    string // where the tunnel connects
	Upstream  string // Home Assistant
	Host      string // the public hostname; any other SNI or Host is refused
	ClientID  string // Google's OAuth client id for this project; any other is refused at /auth/token
	CertFile  string
	KeyFile   string
	Ranges    string // optional file of Google's own CIDRs (not Google Cloud customers'), one per line
	RangeMode string // "log" records whether each client is in Ranges; "enforce" refuses those that aren't
}

func loadConfig() config {
	host := env("RELAY_HOST", "hass-google.brensch.com")
	return config{
		Listen:    env("RELAY_LISTEN", ":8443"),
		Upstream:  strings.TrimRight(env("RELAY_UPSTREAM", "http://homeassistant:8123"), "/"),
		Host:      host,
		ClientID:  os.Getenv("RELAY_CLIENT_ID"),
		CertFile:  env("RELAY_CERT", "/certs/"+host+".crt"),
		KeyFile:   env("RELAY_KEY", "/certs/"+host+".key"),
		Ranges:    os.Getenv("RELAY_GOOGLE_RANGES"),
		RangeMode: env("RELAY_GOOGLE_RANGES_MODE", "log"),
	}
}

func env(k, def string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return def
}

func main() {
	slog.SetDefault(slog.New(slog.NewTextHandler(os.Stdout, nil)))
	cfg := loadConfig()
	if cfg.ClientID == "" {
		slog.Error("RELAY_CLIENT_ID is required")
		os.Exit(1)
	}
	if cfg.RangeMode != "log" && cfg.RangeMode != "enforce" {
		slog.Error("RELAY_GOOGLE_RANGES_MODE must be log or enforce", "got", cfg.RangeMode)
		os.Exit(1)
	}
	certs := &certLoader{cert: cfg.CertFile, key: cfg.KeyFile}
	if _, err := certs.get(); err != nil {
		slog.Error("certificate", "err", err)
		os.Exit(1)
	}
	ranges := &rangeSet{path: cfg.Ranges}
	if cfg.Ranges != "" {
		if err := ranges.reload(); err != nil {
			slog.Error("google ranges", "err", err)
			os.Exit(1)
		}
		if cfg.RangeMode == "enforce" && ranges.empty() {
			slog.Error("google ranges: enforcing an empty list would refuse everyone")
			os.Exit(1)
		}
	} else if cfg.RangeMode == "enforce" {
		slog.Error("RELAY_GOOGLE_RANGES_MODE=enforce needs RELAY_GOOGLE_RANGES")
		os.Exit(1)
	}

	ln, err := net.Listen("tcp", cfg.Listen)
	if err != nil {
		slog.Error("listen", "err", err)
		os.Exit(1)
	}
	tlsCfg := &tls.Config{
		MinVersion: tls.VersionTLS12,
		NextProtos: []string{"http/1.1"},
		GetCertificate: func(hello *tls.ClientHelloInfo) (*tls.Certificate, error) {
			if !strings.EqualFold(hello.ServerName, cfg.Host) {
				return nil, errors.New("unknown server name")
			}
			return certs.get()
		},
	}
	srv := &http.Server{
		Handler:           newRelay(cfg, ranges, &http.Client{Transport: upstreamTransport(), Timeout: 28 * time.Second}),
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       15 * time.Second,
		WriteTimeout:      35 * time.Second,
		IdleTimeout:       60 * time.Second,
		MaxHeaderBytes:    16 << 10,
		TLSNextProto:      map[string]func(*http.Server, *tls.Conn, http.Handler){}, // HTTP/1.1 only
		ErrorLog:          slog.NewLogLogger(slog.Default().Handler(), slog.LevelDebug),
	}
	go func() {
		for range time.Tick(time.Minute) {
			if cfg.Ranges != "" {
				if err := ranges.reload(); err != nil {
					slog.Error("google ranges", "err", err)
				}
			}
		}
	}()
	slog.Info("relay listening", "addr", cfg.Listen, "host", cfg.Host, "upstream", cfg.Upstream, "ranges", cfg.RangeMode)
	err = srv.Serve(tls.NewListener(&proxyListener{Listener: newLimitListener(ln, 64), timeout: 5 * time.Second}, tlsCfg))
	slog.Error("serve", "err", err)
	os.Exit(1)
}

func upstreamTransport() *http.Transport {
	return &http.Transport{
		Proxy:                  nil,
		DialContext:            (&net.Dialer{Timeout: 5 * time.Second}).DialContext,
		MaxIdleConns:           4,
		IdleConnTimeout:        30 * time.Second,
		ResponseHeaderTimeout:  25 * time.Second,
		DisableCompression:     true,
		MaxResponseHeaderBytes: 32 << 10,
	}
}

// ---- requests ----

const (
	fulfillmentPath = "/api/google_assistant"
	tokenPath       = "/auth/token"
	maxRequest      = 64 << 10
	maxResponse     = 8 << 20 // a SYNC lists every exposed device
)

var (
	bearerRE  = regexp.MustCompile(`^Bearer [A-Za-z0-9._~+/=-]{20,}$`)
	intents   = map[string]bool{"action.devices.SYNC": true, "action.devices.QUERY": true, "action.devices.EXECUTE": true, "action.devices.DISCONNECT": true}
	tokenKeys = map[string]bool{"grant_type": true, "code": true, "refresh_token": true, "client_id": true, "client_secret": true, "redirect_uri": true}
)

type relay struct {
	cfg      config
	ranges   *rangeSet
	client   *http.Client
	perIP    *limiters
	tokenIP  *limiters
	logLimit *bucket
}

func newRelay(cfg config, ranges *rangeSet, client *http.Client) *relay {
	return &relay{
		cfg:    cfg,
		ranges: ranges,
		client: client,
		// Google sends a few requests per command and a report now and then; people don't talk to it this fast.
		perIP:    newLimiters(120, time.Minute, 30),
		tokenIP:  newLimiters(10, time.Minute, 5),
		logLimit: newBucket(20, time.Second, 20),
	}
}

type rejection struct {
	status int
	reason string
}

func (r *relay) ServeHTTP(w http.ResponseWriter, req *http.Request) {
	start := time.Now()
	ip := clientIP(req)
	inGoogle := r.ranges.contains(ip)
	w.Header().Set("Cache-Control", "no-store")

	status, n, rej := r.handle(w, req, ip, inGoogle)
	if rej != nil {
		http.Error(w, http.StatusText(rej.status), rej.status)
		status = rej.status
		if !r.logLimit.allow() {
			return // a flood of refusals shouldn't become a flood of log lines
		}
	}
	attrs := []any{"ip", ip.String(), "google", inGoogle, "method", req.Method, "path", clip(req.URL.Path, 64),
		"status", status, "ms", time.Since(start).Milliseconds(), "bytes", n}
	if rej != nil {
		attrs = append(attrs, "refused", rej.reason)
	}
	slog.Info("request", attrs...)
}

func (r *relay) handle(w http.ResponseWriter, req *http.Request, ip netip.Addr, inGoogle bool) (int, int64, *rejection) {
	if !r.perIP.allow(ip) {
		return 0, 0, &rejection{http.StatusTooManyRequests, "rate"}
	}
	if r.cfg.RangeMode == "enforce" && !inGoogle {
		return 0, 0, &rejection{http.StatusForbidden, "not google"}
	}
	if !strings.EqualFold(req.Host, r.cfg.Host) {
		return 0, 0, &rejection{http.StatusNotFound, "host"}
	}
	if req.URL.RawQuery != "" || req.URL.Fragment != "" {
		return 0, 0, &rejection{http.StatusNotFound, "query"}
	}
	// The request target exactly as sent, so no encoding of a path (%67oogle, //, ..) is ever a second spelling.
	var build func(*http.Request, []byte) (*http.Request, *rejection)
	switch req.RequestURI {
	case fulfillmentPath:
		build = r.fulfillment
	case tokenPath:
		if !r.tokenIP.allow(ip) {
			return 0, 0, &rejection{http.StatusTooManyRequests, "token rate"}
		}
		build = r.token
	default:
		return 0, 0, &rejection{http.StatusNotFound, "path"}
	}
	if req.Method != http.MethodPost {
		return 0, 0, &rejection{http.StatusMethodNotAllowed, "method"}
	}
	if req.ContentLength > maxRequest {
		return 0, 0, &rejection{http.StatusRequestEntityTooLarge, "size"}
	}
	body, err := io.ReadAll(http.MaxBytesReader(w, req.Body, maxRequest))
	if err != nil {
		return 0, 0, &rejection{http.StatusBadRequest, "body"}
	}
	up, rej := build(req, body)
	if rej != nil {
		return 0, 0, rej
	}
	up.Header.Set("X-Forwarded-For", ip.String())
	up.Header.Set("X-Forwarded-Proto", "https")
	up.Header.Set("X-Forwarded-Host", r.cfg.Host)
	if ua := req.Header.Get("User-Agent"); ua != "" {
		up.Header.Set("User-Agent", clip(ua, 200))
	}

	resp, err := r.client.Do(up)
	if err != nil {
		return 0, 0, &rejection{http.StatusBadGateway, "upstream"}
	}
	defer resp.Body.Close()
	out, err := io.ReadAll(io.LimitReader(resp.Body, maxResponse+1))
	if err != nil || len(out) > maxResponse {
		return 0, 0, &rejection{http.StatusBadGateway, "upstream body"}
	}
	if ct := resp.Header.Get("Content-Type"); ct != "" {
		w.Header().Set("Content-Type", ct)
	}
	w.Header().Set("Content-Length", fmt.Sprint(len(out)))
	w.WriteHeader(resp.StatusCode)
	n, _ := w.Write(out)
	return resp.StatusCode, int64(n), nil
}

// fulfillment: a JSON object with a requestId and inputs that each name one of the four smart home intents,
// re-encoded, and the bearer token Home Assistant checks.
func (r *relay) fulfillment(req *http.Request, body []byte) (*http.Request, *rejection) {
	if !mediaType(req, "application/json") {
		return nil, &rejection{http.StatusUnsupportedMediaType, "content type"}
	}
	auth := req.Header.Get("Authorization")
	if len(auth) > 4096 || !bearerRE.MatchString(auth) || len(req.Header.Values("Authorization")) != 1 {
		return nil, &rejection{http.StatusUnauthorized, "bearer"}
	}
	clean, err := cleanFulfillment(body)
	if err != nil {
		return nil, &rejection{http.StatusBadRequest, "fulfillment: " + err.Error()}
	}
	up, err := http.NewRequestWithContext(req.Context(), http.MethodPost, r.cfg.Upstream+fulfillmentPath, bytes.NewReader(clean))
	if err != nil {
		return nil, &rejection{http.StatusInternalServerError, "build"}
	}
	up.Header.Set("Content-Type", "application/json")
	up.Header.Set("Authorization", auth)
	return up, nil
}

func cleanFulfillment(body []byte) ([]byte, error) {
	dec := json.NewDecoder(bytes.NewReader(body))
	dec.UseNumber()
	var msg map[string]any
	if err := dec.Decode(&msg); err != nil {
		return nil, errors.New("not a JSON object")
	}
	if dec.More() {
		return nil, errors.New("trailing data")
	}
	if id, ok := msg["requestId"].(string); !ok || id == "" || len(id) > 256 {
		return nil, errors.New("requestId")
	}
	inputs, ok := msg["inputs"].([]any)
	if !ok || len(inputs) == 0 || len(inputs) > 16 {
		return nil, errors.New("inputs")
	}
	for _, in := range inputs {
		m, ok := in.(map[string]any)
		if !ok {
			return nil, errors.New("input")
		}
		if intent, _ := m["intent"].(string); !intents[intent] {
			return nil, errors.New("intent")
		}
	}
	return json.Marshal(msg)
}

// token: a form with exactly one of each field, Google's client id, and an authorization code or refresh token.
// Home Assistant gets only grant_type, client_id and the code or refresh token.
func (r *relay) token(req *http.Request, body []byte) (*http.Request, *rejection) {
	if !mediaType(req, "application/x-www-form-urlencoded") {
		return nil, &rejection{http.StatusUnsupportedMediaType, "content type"}
	}
	if req.Header.Get("Authorization") != "" {
		return nil, &rejection{http.StatusBadRequest, "credentials belong in the body"}
	}
	form, err := url.ParseQuery(string(body))
	if err != nil {
		return nil, &rejection{http.StatusBadRequest, "form"}
	}
	for k, v := range form {
		if !tokenKeys[k] || len(v) != 1 || len(v[0]) > 4096 {
			return nil, &rejection{http.StatusBadRequest, "field " + clip(k, 32)}
		}
	}
	if form.Get("client_id") != r.cfg.ClientID {
		return nil, &rejection{http.StatusBadRequest, "client_id"}
	}
	out := url.Values{"client_id": {r.cfg.ClientID}}
	switch form.Get("grant_type") {
	case "authorization_code":
		if form.Get("code") == "" {
			return nil, &rejection{http.StatusBadRequest, "code"}
		}
		out.Set("grant_type", "authorization_code")
		out.Set("code", form.Get("code"))
	case "refresh_token":
		if form.Get("refresh_token") == "" {
			return nil, &rejection{http.StatusBadRequest, "refresh_token"}
		}
		out.Set("grant_type", "refresh_token")
		out.Set("refresh_token", form.Get("refresh_token"))
	default:
		return nil, &rejection{http.StatusBadRequest, "grant_type"}
	}
	up, err := http.NewRequestWithContext(req.Context(), http.MethodPost, r.cfg.Upstream+tokenPath, strings.NewReader(out.Encode()))
	if err != nil {
		return nil, &rejection{http.StatusInternalServerError, "build"}
	}
	up.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	return up, nil
}

func mediaType(req *http.Request, want string) bool {
	ct := req.Header.Get("Content-Type")
	if i := strings.IndexByte(ct, ';'); i >= 0 {
		ct = ct[:i]
	}
	return strings.EqualFold(strings.TrimSpace(ct), want)
}

func clientIP(req *http.Request) netip.Addr {
	ap, err := netip.ParseAddrPort(req.RemoteAddr)
	if err != nil {
		return netip.Addr{}
	}
	return ap.Addr().Unmap()
}

func clip(s string, n int) string {
	s = strings.Map(func(r rune) rune {
		if r < 0x20 || r == 0x7f {
			return '?'
		}
		return r
	}, s)
	if len(s) > n {
		return s[:n]
	}
	return s
}

// ---- certificate ----

// certLoader reads the certificate Caddy keeps for the hostname, and again whenever Caddy renews it.
type certLoader struct {
	cert, key string
	mu        sync.Mutex
	loaded    *tls.Certificate
	mtime     time.Time
	checked   time.Time
}

func (c *certLoader) get() (*tls.Certificate, error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.loaded != nil && time.Since(c.checked) < time.Minute {
		return c.loaded, nil
	}
	c.checked = time.Now()
	st, err := os.Stat(c.cert)
	if err != nil {
		if c.loaded != nil {
			return c.loaded, nil
		}
		return nil, err
	}
	if c.loaded != nil && st.ModTime().Equal(c.mtime) {
		return c.loaded, nil
	}
	pair, err := tls.LoadX509KeyPair(c.cert, c.key)
	if err != nil {
		if c.loaded != nil {
			return c.loaded, nil // mid-write; try again next minute
		}
		return nil, err
	}
	c.loaded, c.mtime = &pair, st.ModTime()
	slog.Info("certificate loaded", "expires", pair.Leaf.NotAfter.Format(time.DateOnly))
	return c.loaded, nil
}

// ---- Google's address ranges ----

type rangeSet struct {
	path     string
	mu       sync.RWMutex
	prefixes []netip.Prefix
	mtime    time.Time
}

func (s *rangeSet) reload() error {
	if s.path == "" {
		return nil
	}
	st, err := os.Stat(s.path)
	if err != nil {
		return err
	}
	s.mu.RLock()
	same := st.ModTime().Equal(s.mtime)
	s.mu.RUnlock()
	if same {
		return nil
	}
	f, err := os.Open(s.path)
	if err != nil {
		return err
	}
	defer f.Close()
	var ps []netip.Prefix
	sc := bufio.NewScanner(f)
	for sc.Scan() {
		line := strings.TrimSpace(sc.Text())
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		p, err := netip.ParsePrefix(line)
		if err != nil {
			return fmt.Errorf("%s: %w", s.path, err)
		}
		ps = append(ps, p.Masked())
	}
	if err := sc.Err(); err != nil {
		return err
	}
	s.mu.Lock()
	s.prefixes, s.mtime = ps, st.ModTime()
	s.mu.Unlock()
	slog.Info("google ranges loaded", "prefixes", len(ps))
	return nil
}

func (s *rangeSet) empty() bool {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return len(s.prefixes) == 0
}

func (s *rangeSet) contains(ip netip.Addr) bool {
	s.mu.RLock()
	defer s.mu.RUnlock()
	for _, p := range s.prefixes {
		if p.Contains(ip) {
			return true
		}
	}
	return false
}

// ---- rate limits ----

// bucket is a token bucket: rate per interval, holding at most burst.
type bucket struct {
	mu     sync.Mutex
	tokens float64
	last   time.Time
	rate   float64 // tokens per second
	burst  float64
}

func newBucket(n int, per time.Duration, burst int) *bucket {
	return &bucket{tokens: float64(burst), last: time.Now(), rate: float64(n) / per.Seconds(), burst: float64(burst)}
}

func (b *bucket) allow() bool {
	b.mu.Lock()
	defer b.mu.Unlock()
	now := time.Now()
	b.tokens = min(b.burst, b.tokens+now.Sub(b.last).Seconds()*b.rate)
	b.last = now
	if b.tokens < 1 {
		return false
	}
	b.tokens--
	return true
}

type limiters struct {
	mu      sync.Mutex
	m       map[netip.Addr]*bucket
	n       int
	per     time.Duration
	burst   int
	cleaned time.Time
}

func newLimiters(n int, per time.Duration, burst int) *limiters {
	return &limiters{m: map[netip.Addr]*bucket{}, n: n, per: per, burst: burst, cleaned: time.Now()}
}

func (l *limiters) allow(ip netip.Addr) bool {
	l.mu.Lock()
	if time.Since(l.cleaned) > 10*time.Minute || len(l.m) > 10000 {
		for k, b := range l.m {
			b.mu.Lock()
			idle := time.Since(b.last) > 10*time.Minute
			b.mu.Unlock()
			if idle || len(l.m) > 10000 {
				delete(l.m, k)
			}
		}
		l.cleaned = time.Now()
	}
	b, ok := l.m[ip]
	if !ok {
		b = newBucket(l.n, l.per, l.burst)
		l.m[ip] = b
	}
	l.mu.Unlock()
	return b.allow()
}

// ---- connections ----

// limitListener caps open connections; past the cap new ones wait in the kernel's backlog.
type limitListener struct {
	net.Listener
	sem chan struct{}
}

func newLimitListener(l net.Listener, n int) net.Listener {
	return &limitListener{Listener: l, sem: make(chan struct{}, n)}
}

func (l *limitListener) Accept() (net.Conn, error) {
	l.sem <- struct{}{}
	c, err := l.Listener.Accept()
	if err != nil {
		<-l.sem
		return nil, err
	}
	return &limitConn{Conn: c, release: func() { <-l.sem }}, nil
}

type limitConn struct {
	net.Conn
	once    sync.Once
	release func()
}

func (c *limitConn) Close() error {
	err := c.Conn.Close()
	c.once.Do(c.release)
	return err
}

// proxyListener requires every connection to start with a PROXY protocol v2 header (Caddy on the VM sends one)
// and reports the address in it as the connection's remote address. A connection without a valid one is closed.
// The header is read on the connection's own goroutine (its first Read or RemoteAddr), so a slow one can't hold up
// Accept.
type proxyListener struct {
	net.Listener
	timeout time.Duration
}

func (l *proxyListener) Accept() (net.Conn, error) {
	c, err := l.Listener.Accept()
	if err != nil {
		return nil, err
	}
	return &proxyConn{Conn: c, timeout: l.timeout}, nil
}

type proxyConn struct {
	net.Conn
	timeout time.Duration
	once    sync.Once
	remote  net.Addr
	err     error
	br      *bufio.Reader
}

func (c *proxyConn) init() {
	c.once.Do(func() {
		c.Conn.SetReadDeadline(time.Now().Add(c.timeout))
		c.br = bufio.NewReaderSize(c.Conn, 512)
		var addr netip.AddrPort
		addr, c.err = readProxyV2(c.br)
		// The deadline stays: net/http sets its own for the TLS handshake and each request after this.
		if c.err != nil {
			c.Conn.Close()
			return
		}
		c.remote = net.TCPAddrFromAddrPort(addr)
	})
}

func (c *proxyConn) Read(p []byte) (int, error) {
	c.init()
	if c.err != nil {
		return 0, c.err
	}
	return c.br.Read(p)
}

func (c *proxyConn) RemoteAddr() net.Addr {
	c.init()
	if c.err != nil {
		return &net.TCPAddr{IP: net.IPv4zero} // the connection is already closed; nothing will be served on it
	}
	return c.remote
}

var proxyV2Sig = []byte("\r\n\r\n\x00\r\nQUIT\n")

// readProxyV2 reads a PROXY v2 header for a proxied TCP connection and returns its source address. LOCAL commands,
// UDP, unix sockets and v1 headers are all refused: the VM's Caddy only ever sends PROXY over TCP.
func readProxyV2(r *bufio.Reader) (netip.AddrPort, error) {
	var hdr [16]byte
	if _, err := io.ReadFull(r, hdr[:]); err != nil {
		return netip.AddrPort{}, fmt.Errorf("proxy header: %w", err)
	}
	if !bytes.Equal(hdr[:12], proxyV2Sig) {
		return netip.AddrPort{}, errors.New("proxy header: no v2 signature")
	}
	if hdr[12] != 0x21 { // version 2, PROXY
		return netip.AddrPort{}, errors.New("proxy header: not a v2 PROXY command")
	}
	n := int(binary.BigEndian.Uint16(hdr[14:16]))
	if n > 216 {
		return netip.AddrPort{}, errors.New("proxy header: too long")
	}
	body := make([]byte, n)
	if _, err := io.ReadFull(r, body); err != nil {
		return netip.AddrPort{}, fmt.Errorf("proxy header: %w", err)
	}
	switch hdr[13] {
	case 0x11: // TCP over IPv4: src, dst, src port, dst port
		if n < 12 {
			return netip.AddrPort{}, errors.New("proxy header: short IPv4 addresses")
		}
		return netip.AddrPortFrom(netip.AddrFrom4([4]byte(body[0:4])), binary.BigEndian.Uint16(body[8:10])), nil
	case 0x21: // TCP over IPv6
		if n < 36 {
			return netip.AddrPort{}, errors.New("proxy header: short IPv6 addresses")
		}
		return netip.AddrPortFrom(netip.AddrFrom16([16]byte(body[0:16])).Unmap(), binary.BigEndian.Uint16(body[32:34])), nil
	default:
		return netip.AddrPort{}, errors.New("proxy header: not TCP")
	}
}
