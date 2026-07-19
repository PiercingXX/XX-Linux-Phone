#!/bin/bash
# GitHub.com/PiercingXX

# Define colors for whiptail

# Function to check if a command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Function to cache sudo credentials
cache_sudo_credentials() {
    echo "Caching sudo credentials for script execution..."
    sudo -v
    # Keep sudo credentials fresh for the duration of the script
    (while true; do sudo -n true; sleep 60; kill -0 "$$" || exit; done 2>/dev/null &)
}

# Checks for active network connection
if [[ -n "$(command -v nmcli)" && "$(nmcli -t -f STATE g)" != connected ]]; then
    awk '{print}' <<<"Network connectivity is required to continue."
    exit
fi

# Install required tools for TUI
if ! command -v whiptail &> /dev/null; then
    echo -e "${YELLOW}Installing whiptail...${NC}"
    sudo apt install whiptail -y
fi

username=$(id -u -n 1000)
builddir=$(pwd)

# Cache sudo credentials
cache_sudo_credentials

# Function to display a message box
function msg_box() {
    whiptail --msgbox "$1" 0 0 0
}

# Function to display menu
function menu() {
    whiptail --backtitle "GitHub.com/PiercingXX" --title "Main Menu" \
        --menu "Run Options In Order:" 0 0 0 \
        "Install"                               "Install PiercingXX Mod" \
        "Reboot System"                         "Reboot the system" \
        "Exit"                                  "Exit the script" 3>&1 1>&2 2>&3
}
# Main menu loop
while true; do
    clear
    echo -e "${GREEN}Welcome ${username}${NC}\n"
    choice=$(menu)
    case $choice in
        "Install")
            echo -e "${YELLOW}Upgrading System...${NC}"
            # Download Piercing Rice
                echo -e "${YELLOW}Gitting PiercingXX Dots...${NC}"
                rm -rf piercing-dots
                git clone --depth 1 https://github.com/Piercingxx/piercing-dots.git
            # Replace .bashrc
                cp -f piercing-dots/resources/bash/.bashrc /home/"$username"/.bashrc
                source ~/.bashrc
                echo "Upgraded .bashrc."
            # Install Gnome and Dependencies
                echo -e "${YELLOW}Installing Dependencies...${NC}"
                cd scripts || exit
                chmod u+x step-1.sh
                sudo ./step-1.sh
                wait
                cd "$builddir" || exit
                echo -e "${GREEN}Step 1 complete!${NC}"
            # Install Apps & More Dependencies
                echo -e "${YELLOW}Installing Apps & More Dependencies...${NC}"
                cd scripts || exit
                chmod u+x apps.sh
                sudo ./apps.sh
                wait
                cd "$builddir" || exit
            # Bash Stuff
                install_bashrc_support
            # Install Piercing Rice
                cd piercing-dots || exit
                chmod u+x install.sh
                ./install.sh
                wait
                cd "$builddir" || exit
            # Apply Piercing Gnome Customizations as User
                cd piercing-dots/scripts || exit
                ./gnome-customizations.sh
                wait
                cd "$builddir" || exit
            # Clean Up
                rm -rf piercing-dots
            echo -e "${GREEN}PiercingXX Gnome Customizations Applied successfully!${NC}"
            msg_box "System will reboot now."
            sudo reboot
            ;;
        "Reboot System")
            echo -e "${YELLOW}Rebooting system in 3 seconds...${NC}"
            sleep 2
            sudo reboot
            ;;
        "Exit")
            clear
            echo -e "${BLUE}Thank You Handsome!${NC}"
            exit 0
            ;;
    esac
    # Prompt to continue
    while true; do
        read -p "Press [Enter] to continue..." 
        break
    done
done
