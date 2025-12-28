# Changelog

## [v2025.12.28.2] - 2025-12-28
### Added
- **Smarter VRChat Lifecycle Awareness**: The patcher now automatically pauses operations when VRChat is closed and resumes once the game is detected.
- **Proactive Patch Management**: Removed redundant "risky" warnings during runtime. The patcher now silently handles `yt-dlp.exe` regeneration by VRChat during world changes, ensuring the proxy is always correctly applied when in compatible worlds.
- **Enhanced Log Monitoring**: Log scanning now resets and picks up the newest log file immediately upon game restart.

## [v2025.12.28.1] - 2025-12-28
### Added
- **Smart Fallback System**: The patcher now monitors VRChat logs for video loading errors. If a failure is detected, Tier 1 (Proxy) is disabled for 5 minutes.
- **Tiered Fallback Logic**: When Tier 1 is disabled (via logs or health check), the system now attempts Tier 2 (latest yt-dlp) before falling back to Tier 3 (original VRChat yt-dlp).
- **Proxy Health Check**: The wrapper now performs a quick HEAD request to the proxy server before rewriting URLs. If the proxy is unreachable, it automatically falls back to Tier 2.
- **Wrapper State Management**: Introduced `wrapper_state.json` to coordinate fallback status between the patcher and the wrapper.

## [v2025.12.28.0] - 2025-12-28
### Added
- **Game Detection**: The patcher now detects if VRChat is running and logs a warning before attempting risky file operations.
- **Double Backup System**: A secondary secure backup (`yt-dlp-og-secure.exe`) is now created alongside the primary backup (`yt-dlp-og.exe`) to prevent data loss.
- **Enhanced Safety**: Added strict size checks to ensure valid backups are never overwritten by corrupted or wrapper files.
- **Automatic Restore**: The system can now automatically restore a missing or corrupted primary backup from the secure backup.