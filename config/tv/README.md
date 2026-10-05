# Living-room TV (Sony Bravia XBR-75X900H)

Shows up as "BRAVIA VH1", Android TV 12, at 192.168.1.14 (reserved on the router).
It's run as an appliance: 126 of Sony's, Google's and Android's packages are disabled
(`packages.txt`), including everything that updates it, so it stays exactly as it is.
Nothing is uninstalled; a factory reset or `tv.sh adb shell pm enable <pkg>` brings
anything back.

Home Assistant talks to it through Android TV Remote (`media_player.tv`: power and
the foreground app, pushed) and Google Cast (`media_player.tv_cast`: playing/paused,
used by the TV lights automation). Neither needs ADB or anything below.

## ADB

Everything here goes through `tv.sh`, which runs adb in a throwaway container with
the key in `~/.config/adb` (already approved on the TV). On the TV first:
Settings → Device Preferences → Developer options → **USB debugging: on**.
(Developer options appears after pressing Build 7 times under Device Preferences → About.)
Switch it back off when done; it's a remote-control port on the network.

    config/tv/tv.sh status            # anything from packages.txt that's enabled again
    config/tv/tv.sh apply             # disable everything in packages.txt
    config/tv/tv.sh enable-updates    # just the [updates] group on
    config/tv/tv.sh disable-updates   # and off again
    config/tv/tv.sh adb shell ...     # anything else

## Updating everything

Updates are off on purpose. When you do want them (a broken app, a security fix,
a new feature):

1. Turn on USB debugging on the TV (above).
2. Turn updating back on:

       config/tv/tv.sh enable-updates

   This enables the Play Store, Sony's firmware updater and its notification centre.
3. **Firmware:** on the TV, Settings → System → About → System software update →
   Check for update. Install it and let the TV restart (it can restart more than once).
4. **Apps:** open the Play Store on the TV, then your profile icon → Manage apps &
   games → Updates → Update all. Give it a few minutes; it updates itself first.
5. **Projectivy** (the home screen) was installed from its developer's GitHub, not
   the Play Store, so the Play Store won't update it:
   download the newest `ProjectivyLauncher-*-xda-release.apk` from
   https://github.com/spocky/miproja1/releases and

       config/tv/tv.sh adb install -r /path/to/ProjectivyLauncher-....apk

   (`-r` installs over the current one and keeps its settings.) Or, once the Play
   Store has a newer version than the installed one, update it there instead; that
   moves it back to Play Store updates.
6. A firmware update can switch some disabled packages back on, and re-select
   Google's launcher. Check and put things back:

       config/tv/tv.sh status
       config/tv/tv.sh apply
       config/tv/tv.sh adb shell cmd package set-home-activity com.spocky.projengmenu/.ui.home.MainActivity

7. Turn updating off again and restart the TV to clear memory:

       config/tv/tv.sh disable-updates
       config/tv/tv.sh adb reboot

8. Check Home Assistant still sees the TV (`media_player.tv` and
   `media_player.tv_cast` not unavailable), then turn USB debugging off.

## What's disabled and why

See `packages.txt` for every package with a one-line reason. By group:

| Group | What | Count |
|---|---|---|
| updates | Play Store, Sony firmware updater and notifications | 6 |
| launcher | Google's home screen and its recommendations (Projectivy instead) | 2 |
| tracking | Samba TV (on-screen content recognition sold to advertisers), Sony and Qterics telemetry | 4 |
| promos-and-demo | Sony promotions, Sony Select, store demo mode | 5 |
| unused-apps | Netflix, Prime Video, Apple TV, Disney+, YouTube Music, Play Games, Google feedback/movies | 9 |
| voice | Google Assistant and Sony voice search (the mic button does nothing) | 4 |
| antenna-irbox-dlna | ATSC 3.0 tuner, interactive TV, CI card, IR blaster / set-top-box control, DLNA | 12 |
| sony-cloud-and-web | Sony cloud/IoT and remote start, Sony network API, web-app runtimes (Vewd) | 8 |
| no-camera | Gesture/presence/distance features for Sony's optional camera | 5 |
| hotel-and-pro | Hotel mode, RS-232, vendor protocol, pro settings | 5 |
| extras | Help, privacy notices, smart-speaker settings, TalkBack, text-to-speech, ambient photos, printing, calendar sync | 11 |
| airplay | AirPlay receiver | 1 |
| setup-and-backup | Google/Sony setup wizards, partner/one-time setup, Sony's promo auto-installer, backup services (a factory reset re-enables them) | 11 |
| broadcast | Antenna channel setup, broadcast clock sync, broadcast ratings | 5 |
| sony-extras-2 | Eco dashboard, licences, bug-report/log tools, service menu, headphone spatial audio, one-off calibration screens, USB media player/recorder/popups, launcher customiser, Sony account auth, da.service | 18 |
| b2b-rest | Hotel/pro leftovers: admin password, IP control, Node.js web runtime | 7 |
| android-extras | Screensaver, sound picker, HTML viewer, captive-portal/hotspot login, VPN/proxy dialogs, Settings search suggestions, certificate installer, link verification; MediaTek media-server agent (both crashed at boot once their dependencies were off) | 13 |

**Kept**, because something you use needs them: picture/audio/inputs and Sony's TV
app (`tvx`, plus the basic tuner it expects), Settings, Google Play Services (YouTube
sign-in, Chromecast), Chromecast (`mediashell`), the Android TV Remote service, Bravia
Sync (HDMI-CEC, for a soundbar/console), the keyboard, Bluetooth, the core
Android/Sony/MediaTek system, and the b2b framework extension and device-admin settings.
Kept apps: YouTube, YouTube TV, Stremio, Rumble, Spotify, Projectivy.

**Also set:** Android animation speed 0.5x (window, transition and animator scales),
for snappier menus. Undo: `tv.sh adb shell settings put global window_animation_scale 1`
(and the same for `transition_animation_scale` and `animator_duration_scale`).

**Why:** the TV has 3 GB of RAM. Before (2026-10-05) it had ~380 MB available with
swap full, so Android kept killing and relaunching apps; CPU was mostly idle. After
disabling all this and a restart: ~1.46 GB available, swap empty, and a boot with no
crashes. Checked after each round: home (Projectivy), Settings, all five HDMI inputs,
YouTube, Chromecast, the Android TV Remote service and Home Assistant's connection.
