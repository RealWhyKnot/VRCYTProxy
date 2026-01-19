# Changelog

## [v2026.01.18.0] - 2026-01-18
### Added
- **Intelligent Fallback**: Per-URL error tracking with tier escalation (Proxy -> Tier 2 -> Tier 3).
- **Last-Resort Proxy**: Final attempt at Tier 1 (Proxy) if Tier 3 fails, even for non-standard URLs.
- **Expanded Support**: Added Twitch and VRCDN to proxied domains.
- **Conda Build**: Migrated build system to use Conda for better environment stability.
### Improved
- **Error Detection**: High-precision URL capture from `[AVProVideo]` log entries for accurate failure attribution.

## [v2025.12.30.0] - 2025-12-30
### Fixed
- **OS Error 22 (Invalid Argument)**: Resolved a crash in the redirector caused by `print` calls failing when VRChat closed its output pipe prematurely. All communication with VRChat is now wrapped in safety checks.
- **Subprocess Robustness**: Enhanced `attempt_executable` with automated argument sanitization (removing null bytes) and Win32 `CREATE_NO_WINDOW` flags to improve reliability when launching bundled tools.
- **Improved Logging**: Added detailed error reporting for failed process launches and stdout pipe closures.

## [v2025.12.29.2] - 2025-12-29
### Added
- **Externalized Patterns**: Video error and instance detection patterns are now stored in `patcher_config.json`, allowing for updates without code changes.
- **High-Frequency Monitoring**: Refactored VRChat log monitoring into a dedicated 100ms background thread. This ensures millisecond-level responsiveness to world changes and video errors.
- **Tiered Fallback System**: Proxy errors now trigger escalating fallback durations (1m for transient, 5m for standard, 15m/1h for repeated).
- **Smart Error Reset**: The wrapper now resets the consecutive error count upon a successful proxy resolution, preventing permanent escalation from old failures.
- **Enhanced Error Detection**: Added support for detecting `[VideoTXL]` errors and specific `5xx` proxy server responses for immediate fallback.

## [v2025.12.29.1] - 2025-12-29
### Fixed
- **Dynamic Log Discovery**: The patcher now continuously checks for newer log files during a session. This prevents it from getting stuck on an old log file if the current session's log is created slightly after the game process is detected.

## [v2025.12.29.0] - 2025-12-29
### Fixed
- **Log Selection**: Improved log file discovery by sorting by filename (lexicographical) instead of modification time. This ensures the newest VRChat log is always targeted correctly based on its timestamped naming convention.

## [v2025.12.28.11] - 2025-12-28
### Changed
- **Dependency Update**: Updated `yt-dlp` to 2025.12.08 and `Deno` to v2.6.3 for improved video resolution and stability.

## [v2025.12.28.10] - 2025-12-28
### Fixed
- **Build Stabilization**: Minor fixes to the patcher logic and build process verification.

## [v2025.12.28.9] - 2025-12-28
### Changed
- **Full SHA-256 Transition**: Removed all remaining file-size heuristics and transitioned entirely to SHA-256 hash verification for identifying original VRChat files, wrappers, and validating backups.
- **Enhanced Logging**: Updated all log messages to display SHA-256 hash fragments instead of file sizes for better technical verification.

## [v2025.12.28.8] - 2025-12-28
### Improved
- **Smart Error Attribution**: The patcher now verifies if a video error in the VRChat logs is actually related to the proxy (`whyknot.dev`) before triggering a fallback. This prevents unrelated video failures from disabling the proxy.

## [v2025.12.28.7] - 2025-12-28
### Changed
- **URL Format**: Updated the proxy URL base from `https://proxy.whyknot.dev` to `https://whyknot.dev`.

## [v2025.12.28.6] - 2025-12-28
### Added
- **SHA-256 Verification**: Replaced unreliable file-size checks with SHA-256 hash verification. The patcher now precisely identifies its own wrapper and ensures that VRChat's original files are never misidentified or accidentally overwritten.
- **Enhanced Backup Safety**: Backups and restorations now verify file integrity via hashes, preventing "wrapper-on-wrapper" backups or incorrect file restorations.

## [v2025.12.28.5] - 2025-12-28
### Improved
- **Build Script**: Updated `build.ps1` to use `python -m pip` for upgrading pip, ensuring smoother automated dependency management on Windows.

## [v2025.12.28.4] - 2025-12-28
### Fixed
- **Game Detection**: Switched to `tasklist` as the primary process detection method to improve reliability across different Windows installations where `wmic` might be deprecated or slow.

## [v2025.12.28.3] - 2025-12-28
### Added
- **PID-Locked Monitoring**: Patcher now uniquely identifies VRChat sessions via PID/creation time, preventing log ghosting across restarts.
- **Support File Health Check**: Periodic verification ensuring all wrapper dependencies (`deno.exe`, etc.) remain in the `Tools` directory.
- **Proactive File Watchdog**: High-frequency verification of `yt-dlp.exe` to instantly re-apply the patch if VRChat restores the original file.
- **Atomic Session Discovery**: Reset log parsing state immediately upon detecting a new game process.

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