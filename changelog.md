# Changelog

## [v2025.12.28.0] - 2025-12-28
### Added
- **Game Detection**: The patcher now detects if VRChat is running and logs a warning before attempting risky file operations.
- **Double Backup System**: A secondary secure backup (`yt-dlp-og-secure.exe`) is now created alongside the primary backup (`yt-dlp-og.exe`) to prevent data loss.
- **Enhanced Safety**: Added strict size checks to ensure valid backups are never overwritten by corrupted or wrapper files.
- **Automatic Restore**: The system can now automatically restore a missing or corrupted primary backup from the secure backup.
