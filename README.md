# VRCYTProxy - VRChat YouTube URL Patcher

VRCYTProxy is a tool designed to work around issues with playing YouTube videos in VRChat, particularly in instances where YouTube URLs might be blocked or fail to resolve. It works by dynamically patching VRChat's `yt-dlp.exe` to redirect YouTube video requests to a proxy server.

## The Problem

VRChat uses a tool called `yt-dlp.exe` to get the direct video stream URL from a YouTube link. Sometimes, this process can fail. Additionally, for security reasons, VRChat restricts the domains that video players can access in public instances. This means that even if you use a proxy, it won't work in public worlds unless that proxy's domain is whitelisted by VRChat.

## How It Works

This project consists of two main components:

1.  **The Patcher (`patcher.exe`)**: This is a smart patcher that you run in the background while playing VRChat. It constantly monitors your VRChat log files to see what kind of instance you are in.
    *   **Private Worlds**: When you are in a private world (your own, a friend's, etc.), the patcher automatically replaces VRChat's `yt-dlp.exe` with our custom redirector.
    *   **Public Worlds**: When you join a public or group world, the patcher automatically restores the original `yt-dlp.exe`. This is done to ensure compliance with VRChat's security model, as the proxy URL is not whitelisted.

2.  **The Redirector (`yt-dlp-redirect.exe`)**: This is the custom executable that gets renamed to `yt-dlp.exe`. When VRChat calls it to resolve a YouTube URL, it doesn't download the video itself. Instead, it rewrites the URL to point to a proxy server (`https://vrc.whyknot.dev`) and returns the new URL to VRChat. For any non-YouTube URLs, it simply passes the request to the original `yt-dlp.exe` (which was backed up as `yt-dlp-og.exe`).

This dynamic, instance-aware patching ensures that the proxy is only used when it's allowed, providing a seamless experience without compromising security.

## Usage

1.  Download the latest release from the [GitHub Releases](https://github.com/RealWhyKnot/VRCYTProxy/releases) page.
2.  Extract the downloaded `.zip` file somewhere convenient.
3.  Run `patcher.exe`.
4.  A console window will appear, showing the patcher's status. You can minimize this window.
5.  Launch VRChat and enjoy! The patcher will handle everything in the background.

To stop the patcher, simply close the console window. It will automatically clean up and restore the original VRChat files.

## How to Run

After building from source or extracting a release `.zip` file, you will have a single application folder (e.g., `dist/` or the extracted contents of `VRCYTProxy-Windows.zip`). The structure will look like this:

```
<application_folder>/
├── patcher.exe
├── resources/
│   └── wrapper_files/
│       ├── main.exe
│       └── ...
└── ... (many other dependency files)
```

To run the application, simply execute `patcher.exe` from within this main application folder. It will open a console window and begin monitoring VRChat. All the other files and folders must be kept in the same directory as `patcher.exe` for it to function correctly.

## Building from Source

If you want to build the project yourself, you'll need Python 3. A single script is provided to automate the entire build process, including setting up the environment and installing dependencies.

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/RealWhyKnot/VRCYTProxy.git
    cd VRCYTProxy
    ```

2.  **Run the build script for your OS:**

    *   **On Windows:**
        ```bash
        build.bat
        ```
    *   **On macOS or Linux:**
        ```bash
        chmod +x build.sh
        ./build.sh
        ```

3.  **Find the output:**
    Once the script has finished successfully, you will find the complete, runnable application in the `dist` directory.
