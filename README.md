# VRCYTProxy - VRChat YouTube URL Patcher

VRCYTProxy is a tool designed to work around issues with playing YouTube and other videos in VRChat. It works by dynamically patching VRChat's `yt-dlp.exe` to use a more robust, multi-step process for resolving video URLs.

## The Problem

VRChat uses a tool called `yt-dlp.exe` to get the direct video stream URL from a link. This process can fail for two main reasons:

1.  **YouTube Blocks:** VRChat's bundled `yt-dlp` is often outdated, and YouTube's frequent changes can break it, causing videos to fail.
2.  **Other Hosts:** Many other video hosts (like Dailymotion, Vimeo, etc.) may fail to resolve because VRChat's `yt-dlp` is too old to support them.

This patcher provides a solution for both of these issues while respecting VRChat's security model.

## How It Works

This project consists of two main components:

1.  **The Patcher (`patcher.exe`)**: This is a smart patcher that you run in the background. It constantly monitors your VRChat log files to see what kind of instance you are in.

      * **Private Worlds**: When you are in a private world (your own, a friend's, etc.), the patcher automatically replaces VRChat's `yt-dlp.exe` with our custom redirector.
      * **Public Worlds**: When you join a public or group world, the patcher automatically restores the original `yt-dlp.exe`. This is done to ensure compliance with VRChat's security model.

2.  **The Redirector (`yt-dlp-wrapper.exe`)**: This is the custom executable that gets renamed to `yt-dlp.exe`. When VRChat calls it to resolve a URL, it operates on a 3-tier fallback system:

      * **Tier 1 (Proxy - Fast Path):** If the link is a **YouTube URL**, it is *immediately* rewritten to use the `https://proxy.whyknot.dev` server. It never touches an executable, making it almost instant.

      * **Tier 2 (Modern - `yt-dlp-latest.exe`):** If the link is **not for YouTube**, the wrapper first tries to resolve it using a bundled, up-to-date version of `yt-dlp.exe` and its `deno.exe` runtime. This can handle many modern video sites that VRChat's old version can't.

      * **Tier 3 (Fallback - `yt-dlp-og.exe`):** If Tier 2 fails (e.g., the site is unsupported), the wrapper passes the request to VRChat's original `yt-dlp.exe` (which was backed up as `yt-dlp-og.exe`). This ensures that any link that would have worked in VRChat *without* the patch still works.

## For Your Friends (Manual Converter)

Don't want your friends to have to install a program? You can manually convert a YouTube link for them using the public web tool:

**[https://yt.whyknot.dev/](https://yt.whyknot.dev/)**

Just paste a YouTube URL there, get the proxied link, and paste that into a VRChat video player. This only works for YouTube and (like the patcher) only in private worlds.

## Usage

1.  Download the latest release from the [GitHub Releases](https://github.com/RealWhyKnot/VRCYTProxy/releases) page.
2.  Extract the downloaded `.zip` file somewhere convenient.
3.  Run `patcher.exe`.
4.  A console window will appear, showing the patcher's status. You can minimize this window.
5.  Launch VRChat and enjoy\! The patcher will handle everything in the background.

To stop the patcher, simply close the console window. It will automatically clean up and restore the original VRChat files.

## How to Run

After building from source or extracting a release `.zip` file, you will have a single application folder (e.g., `dist/`). The structure will look like this:

```
<application_folder>/
├── patcher.exe
├── wrapper_filelist.json
├── resources/
│   └── wrapper_files/
│       ├── yt-dlp-wrapper.exe
│       ├── yt-dlp-latest.exe
│       ├── deno.exe
│       ├── _internal/
│       └── ... (many other dependency files)
└── ... (patcher's dependency files)
```

To run the application, simply execute `patcher.exe` from within this main application folder. It will open a console window and begin monitoring VRChat. All the other files and folders must be kept in the same directory as `patcher.exe` for it to function correctly.

## Building from Source

The build process is now fully automated with a single PowerShell script. You will need **Python 3.10+** and **PowerShell**.

1.  **Clone the repository:**

    ```bash
    git clone https://github.com/RealWhyKnot/VRCYTProxy.git
    cd VRCYTProxy
    ```

2.  **Run the build script:**

    ```powershell
    # This will download all dependencies, pin their versions, and build the project
    .\build.ps1
    ```

3.  **Find the output:**
    Once the script has finished successfully, you will find the complete, runnable application in the `dist` directory.

### How Dependencies Work

The build script manages its own dependencies (`deno.exe` and `yt-dlp-latest.exe`) in a `vendor/` folder.

  * **First Run:** The script fetches the latest stable versions from GitHub and saves their version tags to a new `vendor_versions.json` file.
  * **Subsequent Runs:** The script reads `vendor_versions.json` to download the *exact* same versions, ensuring a reproducible build.
  * **To get new dependencies:** Run `.\build.ps1 -Force` to ignore the JSON file, fetch the latest versions, and overwrite `vendor_versions.json` with the new tags.