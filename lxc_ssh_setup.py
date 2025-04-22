import os
import subprocess
import sys


def print_progress_bar(iteration, total, length=50, message=None, suffix=None):
    """
    Print a progress bar to the console.
    :param iteration: Current iteration
    :param total: Total iterations
    :param length: Length of the progress bar
    :param message: Message to display above the progress bar
    :param suffix: Suffix to display at the end of the progress bar
    """
    if message:
        sys.stdout.write('\r')  # Return to the start of the line
        sys.stdout.write(' ' * (length + 100) + '\r')  # Clear the current line
        print(message)  # Print the message above the progress bar

    percent = f"{100 * (iteration / float(total)):.1f}"
    filled_len = int(length * iteration // total)
    bar = '█' * filled_len + '-' * (length - filled_len)
    sys.stdout.write(f'\rProgress: |{bar}| {percent}% Complete {iteration}/{total} {" " + suffix if suffix else ""}{" "*20}')
    sys.stdout.flush()
    if iteration == total:
        print()  # Move to the next line


def run_command_silently(command):
    """
    Run a shell command silently, suppressing stdout and stderr.
    :param command: The command to run
    :return: The result of subprocess.run
    """
    return subprocess.run(command, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True)


def get_lxc_containers():
    """
    Get a list of LXC containers
    :return: Dictionary with container names as keys and their IDs and running status as values
    """
    # Run the command to get the list of containers
    result = subprocess.run(['pct', 'list'], capture_output=True, text=True)
    containers = {}
    for line in result.stdout.splitlines()[1:]:
        line = " ".join(line.split()).split()  # split on multiple spaces
        containers[line[2]] = {
            'vmid': line[0],
            'running': line[1] == 'running'
        }
    return containers


def get_linux_version(vmid) -> dict:
    """
    Get the Linux version of the container
    :param vmid: Container ID
    :return: Dictionary with the version information
    """
    result = subprocess.run(f"pct exec {vmid} -- cat /etc/os-release".split(),
                            capture_output=True,
                            text=True)
    return {item[0].lower(): item[1].replace('"', '')
            for item in [
                line.split("=") for line in result.stdout.splitlines()
                ]}


def install_openssh(container) -> tuple[bool, str]:
    """
    Check if OpenSSH is installed in the container and install it if not.
    Also, ensure the SSH service is running and set to start on boot.
    :param container: Container data
    :return: Tuple (True if OpenSSH was installed or configured, False if it was already installed and configured, and a message)
    """
    # Command to check if OpenSSH is installed
    check_command = f"pct exec {container['vmid']} -- sh -c 'command -v sshd > /dev/null 2>&1'"
    check_result = subprocess.run(check_command, shell=True, capture_output=True, text=True)

    if check_result.returncode == 0:
        # Check if the SSH service is running and enabled
        if container['version']['id'] == 'alpine':
            service_check_command = (
                f"pct exec {container['vmid']} -- sh -c "
                f"'rc-update show | grep sshd | grep -q default && rc-service sshd status | grep -q started'"
            )
        else:
            service_check_command = (
                f"pct exec {container['vmid']} -- sh -c "
                f"'systemctl is-enabled ssh && systemctl is-active ssh'"
            )

        service_check_result = subprocess.run(service_check_command, shell=True, capture_output=True, text=True)

        if service_check_result.returncode == 0:
            return False, f"OpenSSH is already installed and properly configured."
        else:
            # Start and enable the SSH service
            if container['version']['id'] == 'alpine':
                configure_command = (
                    f"pct exec {container['vmid']} -- sh -c "
                    f"'rc-update add sshd && rc-service sshd start'"
                )
            else:
                configure_command = (
                    f"pct exec {container['vmid']} -- sh -c "
                    f"'systemctl enable ssh && systemctl start ssh'"
                )
            run_command_silently(configure_command)
            return True, f"SSH service started and enabled."

    # Determine the package manager and install OpenSSH
    if container['version']['id'] == 'alpine':
        install_command = (
            f"pct exec {container['vmid']} -- sh -c "
            f"'apk add --no-cache openssh && rc-update add sshd && rc-service sshd start'"
        )
    elif container['version']['id'] in ['debian', 'ubuntu']:
        install_command = (
            f"pct exec {container['vmid']} -- sh -c "
            f"'apt-get update && apt-get install -y openssh-server && systemctl enable ssh && systemctl start ssh'"
        )
    elif container['version']['id'] in ['centos', 'rhel', 'fedora']:
        install_command = (
            f"pct exec {container['vmid']} -- sh -c "
            f"'yum install -y openssh-server && systemctl enable sshd && systemctl start sshd'"
        )
    else:
        return False, f"Unsupported Linux distribution, {container['version']['id']}."

    # Run the installation command
    install_result = subprocess.run(install_command, shell=True, capture_output=True, text=True)

    if install_result.returncode != 0:
        return False, f"Failed to install OpenSSH in container {container['vmid']}: {install_result.stderr.strip()}"

    return True, f"OpenSSH successfully installed and configured in container {container['vmid']}."


def set_ssh_password_authentication(container, status='no') -> tuple[bool, str]:
    """
    Set ssh password authentication to yes or no
    :param container: Container data
    :param status: yes or no
    :return: tuple (True if changed, False if not, and a message)
    """
    command = (
        f"pct exec {container['vmid']} -- sh -c "
        f"'[ -f /etc/ssh/sshd_config ] && grep PasswordAuthentication /etc/ssh/sshd_config || exit 1'"
    )
    result = subprocess.run(command, shell=True, capture_output=True, text=True)

    # If the file doesn't exist or grep fails, return False
    if result.returncode != 0:
        return True, "SSH not installed or not configured."

    # Check if PasswordAuthentication is already set to the desired status
    for line in result.stdout.splitlines():
        if line.startswith(f'PasswordAuthentication {status}'):
            return False, "Already set to the desired status."

    # If not okay, update the configuration
    command = f"pct exec {container['vmid']} -- sed -E -i 's|^#?(PasswordAuthentication)\\s.*|\\1 {status}|' /etc/ssh/sshd_config"
    subprocess.run(command, shell=True)
    
    # Restart the SSH service
    if container['version']['id'] == 'alpine':
        subprocess.run(f"pct exec {container['vmid']} -- service ssh restart".split())
    else:
        subprocess.run(f"pct exec {container['vmid']} -- systemctl restart ssh".split())
    
    return True, f"PasswordAuthentication set to {status}"


def add_ssh_public_keys(container, key_file='keys.pub', remove_existing=True) -> tuple[bool, str]:
    """
    Add keys from key_file to container using a single pct exec command.
    :param container: Container data
    :param key_file: Path to the file containing public keys
    :param remove_existing: If True, remove existing keys before adding new ones
    :return: Tuple (True if keys were added, False if not, and a message)
    """
    # Check if the key file exists
    try:
        with open(key_file, 'r') as f:
            keys = f.read().strip()
    except FileNotFoundError:
        return False, f"Key file '{key_file}' not found."

    if not keys:
        return False, "Key file is empty."

    # Shell script content
    script_content = """
#!/bin/sh

KEYS="$1"
REMOVE_EXISTING="$2"
AUTHORIZED_KEYS="/root/.ssh/authorized_keys"

# Ensure the .ssh directory exists
mkdir -p /root/.ssh
chmod 700 /root/.ssh

# Read the existing authorized_keys file into memory
if [ -f "$AUTHORIZED_KEYS" ]; then
    EXISTING_KEYS=$(cat "$AUTHORIZED_KEYS")
else
    EXISTING_KEYS=""
fi

# Update the keys in memory
if [ "$REMOVE_EXISTING" = "true" ]; then
    UPDATED_KEYS="$KEYS"
else
    UPDATED_KEYS="$EXISTING_KEYS"
    echo "$KEYS" | while IFS= read -r key; do
        if ! echo "$EXISTING_KEYS" | grep -qxF "$key"; then
            UPDATED_KEYS="$UPDATED_KEYS
$key"
        fi
    done
fi

# Compare the updated keys with the existing keys
if [ "$UPDATED_KEYS" = "$EXISTING_KEYS" ]; then
    # No changes were made
    exit 0
else
    # Changes were made, write the updated keys back to the file
    echo "$UPDATED_KEYS" > "$AUTHORIZED_KEYS"
    chmod 600 "$AUTHORIZED_KEYS"
    exit 1
fi

# If something unexpected happens, return an error code
exit 2
"""

    # Replace $1 and $2 placeholders with the actual values
    script_content = script_content.replace("$1", keys).replace("$2", "true" if remove_existing else "false")

    # Execute the script in the container
    command = f"pct exec {container['vmid']} -- sh -c '{script_content}'"
    result = subprocess.run(command, shell=True, capture_output=True, text=True)

    # Handle the exit code
    if result.returncode == 0:
        return False, f"No changes were made to container."
    elif result.returncode == 1:
        return True, f"SSH keys successfully added to container."
    else:
        return True, f"Failed to add SSH keys to container: {result.stderr.strip()}"


def main():
    print('Getting containers IDs.')
    containers = get_lxc_containers()
    container_count = len(containers)

    print('\nGetting container operating system info:')
    for i, (name, data) in enumerate(containers.items()):
        if data['running']:
            containers[name]['version'] = get_linux_version(data['vmid'])
            message = None
        else:
            message = f"Container {name} ({data['vmid']}) is not running."
        print_progress_bar(i+1, container_count, message=message, suffix=name)

    print('\nInstalling OpenSSH into containers if needed:')
    for i, (name, data) in enumerate(containers.items()):
        if data['running']:
            result, message = install_openssh(containers[name])
            if result:
                message = f"Container {name} ({data['vmid']}) {message}"
            else:
                message = None
        else:
            message = f"Container {name} ({data['vmid']}) is not running."       
        print_progress_bar(i+1, container_count, message=message, suffix=name)

    print('\nDisabling SSH password authentication in containers:')
    for i, (name, data) in enumerate(containers.items()):
        if data['running']:
            result, message = set_ssh_password_authentication(containers[name], 'no')
            if result:
                message = f"Container {name} ({data['vmid']}) {message}"
            else:
                message = None
        else:
            message = f"Container {name} ({data['vmid']}) is not running."       
        print_progress_bar(i+1, container_count, message=message, suffix=name)

    if os.path.exists('keys.pub'):
        print('\nReplacing public SSH keys in containers:')
        for i, (name, data) in enumerate(containers.items()):
            if data['running']:
                result, message = add_ssh_public_keys(containers[name])
                if result:
                    message = f"Container {name} ({data['vmid']}) {message}"
                else:
                    message = None
            else:
                message = f"Container {name} ({data['vmid']}) is not running."       
            print_progress_bar(i+1, container_count, message=message, suffix=name)
    else:
        print('\nReplacing public SSH keys is not possible. Create keys.pub file.')


if __name__ == "__main__":
    main()
