# Sony Bravia (XBR-75X900H, "BRAVIA VH1", Android TV 12) — disabled apps

Disabled over ADB with `pm disable-user --user 0 <package>` on 2026-10-05.
Disabled, not uninstalled: a factory reset or `pm enable <package>` restores any of them.

ADB: Developer options → USB debugging on the TV, then from the server
(key in ~/.config/adb, already approved on the TV):

    docker run --rm --network host -v ~/.config/adb:/root/.android alpine sh -c \
      'apk add -q android-tools && adb connect 192.168.1.14:5555 && adb -s 192.168.1.14:5555 shell pm enable <package>'

| Package | What it was |
|---|---|
| com.google.android.tvlauncher | Google's home screen; Projectivy (com.spocky.projengmenu) is the home app instead |
| tv.samba.ssm, tv.samba.ssm.ui | Samba TV: content recognition of what's on screen, sold to advertisers |
| com.sonyericsson.idd.agent | Sony usage-data collection |
| com.qterics.da.product | Qterics device analytics |
| com.sony.dtv.promos | Sony promotions |
| com.sony.dtv.sonyselect | Sony Select app showcase |
| com.sony.dtv.demosupport, com.sony.dtv.demosystemsupport, com.sony.dtv.multiscreendemo | Retail demo mode |
| com.google.android.tvrecommendations | Recommendation rows for the (disabled) Google launcher |
| com.google.android.feedback | Google feedback |
| com.google.android.play.games | Play Games |
| com.google.android.videos | Google Play Movies / Google TV app |
| com.netflix.ninja, com.netflix.partner.ncm | Netflix and its Sony integration component (unused) |
| com.amazon.amazonvideo.livingroom | Prime Video (unused) |
| com.apple.atve.sony.appletv | Apple TV app (unused; AirPlay is separate and still on) |
| com.disney.disneyplus | Disney+ (unused) |
| com.google.android.youtube.tvmusic | YouTube Music (unused) |
| com.google.android.katniss | Google app: Assistant and voice search on the remote's mic button |

Kept as apps: YouTube, YouTube TV, Stremio, Rumble, Spotify (and Projectivy).

Already disabled by Sony: com.sony.dtv.homekit, com.sony.dtv.livingfit, com.sony.dtv.b2b.softap.

Deliberately left alone: inputs/tuner/picture/audio/system update, Chromecast
(com.google.android.apps.mediashell — HA's kitchen-off-on-play uses it), the
Android TV Remote service (HA's media_player.tv), AirPlay, voice search, and the
b2b/hotel framework (useless at home but close to the core).
