# Palworld Dedicated Server Migration Guide (Local Co-op to Linux Docker)

This guide documents the process of migrating a local Windows Co-op save to a Linux Docker dedicated server, specifically addressing the "Host Save Bug" and newer save format (`PlM`/Oodle) issues.

## 1. Transfer Save Data

1.  **Locate Local Save (Windows):**
    *   Path: `%LOCALAPPDATA%\Pal\Saved\SaveGames\<SteamID>\<SaveID>`
    *   The `<SaveID>` is a long hexadecimal string (e.g., `39629ADA...`).

2.  **Transfer to Server:**
    *   Destination: `/home/brensch/home/data/palworld/Pal/Saved/SaveGames/0/`
    *   Resulting path should be: `.../SaveGames/0/<SaveID>/`

3.  **Set Permissions:**
    *   Ensure the server user (usually UID 1000) owns the files.
    ```bash
    sudo chown -R 1000:1000 /home/brensch/home/data/palworld/Pal/Saved/SaveGames/0/<SaveID>
    ```

## 2. Configure Server

1.  **Update `GameUserSettings.ini`:**
    *   File: `.../Pal/Saved/Config/LinuxServer/GameUserSettings.ini`
    *   Set `DedicatedServerName=<SaveID>` (the folder name you just uploaded).

2.  **Remove `WorldOption.sav` (Crucial):**
    *   If `WorldOption.sav` exists in the save folder, it overrides `PalWorldSettings.ini`.
    *   **Action:** Rename or delete it to allow server settings (like Admin Password) to take effect.
    ```bash
    mv .../<SaveID>/WorldOption.sav .../<SaveID>/WorldOption.sav.bak
    ```

## 3. The "Host Save Fix" (Migrating the Host Character)

**The Problem:** When moving from Co-op to Dedicated, the host's GUID changes from `0000...01` to a real Steam-based GUID. The game forces you to create a new character.

**The Complication:** Newer Palworld saves use `PlM` magic bytes (Oodle compression). The standard `palworld-host-save-fix` and `palworld-save-tools` libraries often fail with "incorrect header check" or "unknown magic bytes".

### Step 3.1: Generate New GUID
1.  Start the server with the migrated save.
2.  Join the server with the Host account.
3.  **Create a new character.**
4.  Disconnect immediately.
5.  Stop the server.

### Step 3.2: Identify GUIDs
*   **Old GUID:** `00000000000000000000000000000001` (Standard for Co-op host).
*   **New GUID:** Look in `.../<SaveID>/Players/`. It will be the new `.sav` file created (e.g., `5786F248...0000.sav`).

### Step 3.3: Run the Patched Fix Tool
**NOTE:** You must use a version of `palworld-host-save-fix` that supports Oodle/PlM compression. Standard `pip install` versions may fail.

1.  **Setup Environment:**
    *   Use a patched version of the tool (e.g., unzipped from `palworld-host-save-fix.zip` provided by community fixes).
    *   Ensure `libooz.so` (or equivalent) is present and accessible if using the raw script, or use the pre-packaged patched tool.

2.  **Run the Script:**
    ```bash
    # Syntax: python3 fix_host_save.py <SavePath> <NewGUID> <OldGUID> <GuildFix>
    
    python3 palworld-host-save-fix-patched/palworld-host-save-fix/fix_host_save.py \
    /home/brensch/home/data/palworld/Pal/Saved/SaveGames/0/<SaveID> \
    <New_GUID_Formatted> \
    00000000000000000000000000000001 \
    False
    ```
    *   *Note on GuildFix:* Set the last argument to `True` if the player ends up in the wrong guild, though `False` is safer for initial attempts.

3.  **Verify:**
    *   The script will backup the save, convert to JSON, swap the GUIDs, and convert back to `.sav`.

## 4. Troubleshooting & Final Steps

1.  **Fix Permissions Again:**
    *   The script runs as root/user, so re-run `chown` on the save folder before starting the server.

2.  **Container Hangs/Crashes:**
    *   If the Docker container hangs on startup (e.g., stuck at `Attaching to palworld` or looping errors):
    *   **Force Recreate:**
        ```bash
        docker compose stop palworld
        docker compose rm -f palworld
        docker compose up -d --force-recreate palworld
        ```

3.  **Admin Password Not Working:**
    *   Ensure `WorldOption.sav` is removed/renamed (see Step 2).
    *   Ensure `PalWorldSettings.ini` is valid (no syntax errors). If corrupted, rename it to `.bak` and let the server regenerate it.
