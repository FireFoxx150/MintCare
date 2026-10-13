#!/bin/bash
# Removes the launcher. Tweaks you applied stay until you switch them off in MintCare.
systemctl --user disable --now mintcare-maintenance.timer 2>/dev/null || true
rm -f "$HOME/.config/systemd/user/mintcare-maintenance."{timer,service}
systemctl --user daemon-reload 2>/dev/null || true
rm -f "$HOME/.local/bin/mintcare" "$HOME/.local/share/applications/mintcare.desktop"
echo "MintCare launcher removed."
