# VRCYTProxy - VRChat YouTube & Stream Patcher

VRCYTProxy is a tool designed to resolve playback issues with YouTube and other video platforms in VRChat. It dynamically patches VRChat's `yt-dlp.exe` to use a robust, multi-step resolution process while ensuring compliance with VRChat's security model.

## The Problem

VRChat relies on a bundled `yt-dlp.exe` to extract direct video stream URLs. This frequently fails because:

1.  **Platform Changes:** YouTube and other sites constantly update their APIs and anti-bot measures, breaking older versions of `yt-dlp`.
2.  **Protocol Support:** VRChat's internal player often lacks support for modern HLS/DASH manifest handling required by many sites today.

VRCYTProxy fixes these issues by introducing a high-performance proxy and a smart fallback chain.

## How It Works

This project consists of two main components:

1.  **The Patcher (`patcher.exe`)**: A background service that monitors VRChat logs to detect your current world instance type.

      * **Private/Friends/Group Instances**: The patcher automatically replaces VRChat's `yt-dlp.exe` with our custom redirector. This includes `Invite`, `Friends`, `Friends+`, `Group`, and `Group Plus` worlds.
      * **Public Worlds**: When you join a `Public` or `Group Public` instance, the patcher automatically restores the original `yt-dlp.exe` to ensure full compliance with VRChat's security guidelines.

2.  **The Redirector (`yt-dlp-wrapper.exe`)**: This component acts as a drop-in replacement for `yt-dlp.exe`. When VRChat requests a URL, it uses a 3-tier system:

      * **Tier 1 (Proxy - High Priority):** For supported domains (**YouTube, Twitch, VRCDN, Discord**), the request is sent to the `whyknot.dev` server. The server resolves the stream using the absolute latest master-branch fixes and returns a stable, proxied HLS stream directly to your VRChat client.
      * **Tier 2 (Modern Local):** If the site is unsupported by Tier 1, it attempts resolution using a bundled, up-to-date version of `yt-dlp.exe` and its `Deno` runtime.
      * **Tier 3 (Legacy Fallback):** If all else fails, it passes the request to VRChat's original `yt-dlp.exe` (backed up as `yt-dlp-og.exe`), ensuring no original functionality is lost.

## Features

- **Automated Instance Detection:** Smoothly switches between patched and original files as you move between worlds.
- **Master Branch Tracking:** The proxy server always uses the absolute latest `yt-dlp` code from the master branch, often fixed weeks before an official release.
- **HLS/TS Support:** Seamlessly handles live streams from VRCDN and Twitch.
- **Smart Rate Limiting:** Avoids 429 errors by intelligently managing VRChat's aggressive HEAD/GET request pairs.

## Usage

1.  Download the latest release from the [GitHub Releases](https://github.com/RealWhyKnot/VRCYTProxy/releases) page.
2.  Extract the `.zip` file.
3.  Run `patcher.exe`. You can minimize the console window.
4.  Launch VRChat. The patcher will handle the rest.

To uninstall, simply close `patcher.exe`. It will automatically restore your original VRChat files before exiting.

## Building from Source

The build process is fully automated and requires **Python 3.13** and **Conda**.

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/RealWhyKnot/VRCYTProxy.git
    cd VRCYTProxy
    ```

2.  **Run the build script:**
    ```powershell
    # This will setup a Conda environment and build all components
    .\build.ps1
    ```

3.  **Smart Dependency Management:**
    The script tracks upstream changes. It will only redownload `Deno` or rebuild `yt-dlp` from master if it detects a new version/commit on GitHub. To force a full rebuild of everything, use `.\build.ps1 -Force`.

## Manual Conversion

If you want to share a link with friends who don't have the patcher, use the web tool:
**[https://whyknot.dev/](https://whyknot.dev/)** (Download section in navbar)
