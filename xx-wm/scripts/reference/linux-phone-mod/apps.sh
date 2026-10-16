#!/bin/bash
# https://github.com/PiercingXX

username=$(id -u -n 1000)
builddir=$(pwd)


# Checks for active network connection
if [[ -n "$(command -v nmcli)" && "$(nmcli -t -f STATE g)" != connected ]]; then
    awk '{print}' <<<"Network connectivity is required to continue."
    exit
fi

echo "Updating"
sudo apt update && upgrade -y
wait

# Installing important things && stuff && some dependencies
    echo "Installing Programs and Drivers"
    sudo apt install cups -y
    sudo apt install seahorse -y
    sudo apt install rename -y
    sudo apt install fwupd -y
    sudo apt install w3m -y
    sudo apt install kitty -y

# Waydroid - check if available in apt, otherwise install from repo
    if sudo apt install waydroid -y; then
        echo "waydroid installed via apt"
    else
        echo "waydroid not available in apt, installing from repo.waydro.id"
        sudo apt install curl ca-certificates -y
        curl -s https://repo.waydro.id | sudo bash
        sudo apt install waydroid -y
    fi

# Install Neovim nightly arm64 from git
    git clone https://github.com/neovim/neovim.git
    cd neovim || exit
    git checkout nightly
    make CMAKE_BUILD_TYPE=Release
    sudo make install
    cd $builddir || exit
# Nvim Depends
    sudo apt install lua5.4 -y
    sudo apt install python3-pip -y
    sudo apt install chafa -y
    sudo apt install ripgrep -y

# Install yazi
    brew install yazi ffmpeg sevenzip jq poppler fd ripgrep fzf zoxide imagemagick font-symbols-only-nerd-font
# Install plugins
    ya pkg add dedukun/bookmarks
    ya pkg add yazi-rs/plugins:mount
    ya pkg add dedukun/relative-motions
    ya pkg add yazi-rs/plugins:chmod
    ya pkg add yazi-rs/plugins:smart-enter
    ya pkg add AnirudhG07/rich-preview
    ya pkg add grappas/wl-clipboard
    ya pkg add Rolv-Apneseth/starship
    ya pkg add uhs-robert/recycle-bin
    ya pkg add yazi-rs/plugins:diff

# Firewall
    sudo apt install ufw -y
    sudo ufw allow OpenSSH
    sudo ufw allow SSH
    sudo ufw enable

## Tailscale
    # Have to install manually, distro isnt recognized yet
    wget https://pkgs.tailscale.com/stable/tailscale_1.90.9_arm64.tgz
    tar xzf tailscale_1.90.9_arm64.tgz
    cd tailscale_1.90.9_arm64/
    # Update service file to use correct binary paths before moving it
    sed -i 's|/usr/sbin/tailscaled|/usr/local/bin/tailscaled|g' systemd/tailscaled.service
    sudo mv tailscale /usr/local/bin/tailscale
    sudo mv tailscaled /usr/local/bin/tailscaled
    sudo mv systemd/tailscaled.service /etc/systemd/system/tailscaled.service
    sudo mv systemd/tailscaled.defaults /etc/default/tailscaled 
    sudo systemctl daemon-reload
    sudo systemctl enable --now tailscaled.service
    echo "Tailscale installed, please run 'sudo tailscale up' to login"
    cd $builddir || exit

# Overkill is underrated 
    sudo apt update && upgrade -y
    wait
    sudo apt full-upgrade -y
    wait
    sudo apt install -f
    wait
    sudo dpkg --configure -a
    sudo apt --fix-broken install -y
    wait
    sudo apt autoremove -y
    sudo apt update && upgrade -y
    wait
    flatpak update -y