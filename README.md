Running this script enables SSH and loads the public keys defined in keys.pub to all LXCs running on the Proxmox host.
- The script enumerates the running LXCs
- Reads the os release data from each
- Installs OpenSSH and enables the service if not installed or running
- Disables SSH password authencation
- Updates the authorized keys
