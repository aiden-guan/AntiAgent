"""Constants and default definitions for AntiAgent."""

# Antigravity Decision Types
DECISION_ALLOW = "allow"
DECISION_DENY = "deny"
DECISION_ASK = "ask"
DECISION_FORCE_ASK = "force_ask"

# Safety Profiles
PROFILE_BALANCED = "balanced"
PROFILE_PARANOID = "paranoid"
PROFILE_AUTONOMOUS = "autonomous"

DEFAULT_PROFILE = PROFILE_BALANCED

# Safe read-only Antigravity tools that never mutate filesystem or execute shell code
SAFE_READ_TOOLS = {
    "view_file",
    "list_dir",
    "grep_search",
    "find_by_name",
    "read_url_content",
    "read_resource",
    "list_resources",
    "get_file_contents",
    "issue_read",
    "pull_request_read",
    "list_issues",
    "list_pull_requests",
    "list_releases",
    "list_tags",
    "list_branches",
    "list_commits",
    "get_commit",
    "get_me",
    "search_code",
    "search_issues",
    "search_pull_requests",
    "search_repositories",
}

# Mutation tools in Antigravity
MUTATING_TOOLS = {
    "run_command",
    "write_to_file",
    "replace_file_content",
    "push_files",
    "create_or_update_file",
    "delete_file",
    "create_pull_request",
    "merge_pull_request",
    "issue_write",
}

# Safe shell command names (first token) that are strictly read-only
SAFE_COMMAND_PREFIXES = {
    # Cross-platform / Unix
    "ls",
    "pwd",
    "cat",
    "head",
    "tail",
    "grep",
    "egrep",
    "fgrep",
    "find",
    "which",
    "where",
    "where.exe",
    "echo",
    "wc",
    "diff",
    "file",
    "uname",
    "date",
    "whoami",
    "printenv",
    "env",
    "stat",
    "basename",
    "dirname",
    "tree",
    "true",
    "false",
    # Windows CMD & PowerShell
    "dir",
    "type",
    "cls",
    "ver",
    "systeminfo",
    "findstr",
    "fc",
    "attrib",
    "hostname",
    "get-childitem",
    "gci",
    "get-content",
    "gc",
    "get-location",
    "gl",
    "write-output",
    "clear-host",
    "select-string",
    "sls",
    "test-path",
    "get-item",
    "gi",
    "get-command",
    "gcm",
    "get-process",
    "gps",
}

# Safe git subcommands (strictly read-only)
SAFE_GIT_SUBCOMMANDS = {
    "status",
    "diff",
    "log",
    "show",
    "rev-parse",
    "describe",
    "stash list",
    "config --get",
    "config -l",
    "config --list",
}

# Dangerous git subcommands that can destroy uncommitted work or rewrite remote history
RISKY_GIT_OPERATIONS = [
    r"git\s+push\s+.*(-f|--force)",
    r"git\s+reset\s+--hard",
    r"git\s+clean\s+-[a-zA-Z]*f",
    r"git\s+branch\s+-[a-zA-Z]*[dD]",
    r"git\s+checkout\s+-[a-zA-Z]*f",
    r"git\s+restore\s+(\.|--staged\s+\.)",
    r"git\s+stash\s+(drop|clear)",
    r"git\s+tag\s+-d",
    r"git\s+remote\s+(set-url|add|remove|rename|rm)",
]

# Patterns that are immediately blocked without prompting (Tier 1 Hard Block)
HARD_DENY_PATTERNS = [
    # System wiping / destructive deletion (Unix) - catches -rf, -fr, -r -f, -f -r, --recursive --force, etc.
    r"(?:sudo\s+)?rm\s+(?:-[a-zA-Z0-9_-]+\s+)*(?:-[a-zA-Z]*[rR][a-zA-Z]*[fF][a-zA-Z]*|-[a-zA-Z]*[fF][a-zA-Z]*[rR][a-zA-Z]*|--recursive\s+--force|--force\s+--recursive|-[a-zA-Z]*[rR][a-zA-Z]*\s+-[a-zA-Z]*[fF][a-zA-Z]*|-[a-zA-Z]*[fF][a-zA-Z]*\s+-[a-zA-Z]*[rR][a-zA-Z]*)\s+(?:-[a-zA-Z0-9_-]+\s+)*(/\s*$|/\s+.*|/\*|/etc|/private/etc|/usr|/bin|/sbin|/var|/private/var|/System|/Library|/root|/proc|/sys|/dev|~/?$|~\*)",
    # System wiping / destructive deletion (Windows CMD & PowerShell)
    r"(?:cmd(?:\.exe)?\s+/c\s+)?\b(del|rmdir|rd)\s+.*(/s|/q).*(c:\\windows|c:\\users|\%systemroot\%|\$env:windir|\$env:systemroot)",
    r"\bRemove-Item\s+.*(-Recurse|-Force).*(c:\\windows|c:\\users|\$env:windir|\$env:systemroot|c:\\\s*$)",
    # Disk formatting / raw device overwrites
    r"\bmkfs\b",
    r"\bdd\s+if=.*of=(/dev/sd[a-z]|/dev/nvme[0-9]|/dev/disk[0-9]|/dev/null\b)",
    r"\bformat\s+[a-zA-Z]:",
    r"\bFormat-Volume\b",
    # Fork bombs
    r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:",
    # Piping untrusted network scripts directly to shell (Unix & Windows PowerShell)
    r"(curl|wget|fetch|http)\s+.*\|\s*(sh|bash|zsh|python[0-9]?|ruby|perl)",
    r"(Invoke-WebRequest|iwr|curl|wget)\s+.*\|\s*(iex|Invoke-Expression|powershell|cmd)",
    r"\biex\s*\(\s*(New-Object\s+Net\.WebClient|Invoke-WebRequest|iwr|curl|irm|Invoke-RestMethod)",
    r"\bcertutil\s+(-urlcache|-split|-f)\s+.*\|\s*(cmd|powershell|sh)",
    # Reverse shells
    r"(\bbash\b.*>&\s*/dev/tcp/|\b(nc|ncat|netcat)\s+(-e\s+|/bin/|\d+\.\d+\.\d+\.\d+)|/bin/sh\s+-i\s+2>&1|\bmkfifo\b.*\|\s*(nc|ncat|netcat)|\bsocat\s+exec:)",
    # SSH, cloud & developer credential exfiltration
    r"(~?/\.ssh/id_[a-zA-Z0-9_]+|\.ssh/id_[a-zA-Z0-9_]+|\.aws/credentials|\.kube/config|\.npmrc|\.pypirc|\.netrc|\.git-credentials)",
    # Direct credential exfiltration via curl/wget/upload
    r"\b(curl|wget)\s+.*(?:-d\s+@|-F\s+.*@|--data-binary\s+@|--post-file[=\s]+).*(\.env|id_rsa|id_ed25519|\.ssh|\.aws|\.kube|\.npmrc|\.pypirc|\.netrc|/etc/shadow)",
    # Windows registry hive / credential dump
    r"\breg\s+save\s+hklm\\(sam|system|security)",
    # Shadow / password / sudoers file dump
    r"(?:sudo\s+)?cat\s+.*(/etc/shadow|/etc/master\.passwd|/etc/sudoers|/private/etc/shadow|/private/etc/master\.passwd)",
    # Tampering with sudoers configuration
    r"(>>|>|visudo|tee)\s+.*(/etc/sudoers|/private/etc/sudoers)",
]

# Sensitive file and directory patterns
SENSITIVE_TARGETS = [
    r"\.env(\.[a-zA-Z0-9_.-]+)?$",
    r"\.ssh(/|\\|$)",
    r"\.aws(/|\\|$)",
    r"\.kube(/|\\|$)",
    r"\.gnupg(/|\\|$)",
    r".*\.pem$",
    r".*\.key$",
    r".*\.pfx$",
    r".*\.p12$",
    r"id_rsa",
    r"id_ed25519",
    r"id_ecdsa",
    r"id_dsa",
    r"known_hosts",
    r"authorized_keys",
    r"(^|/|\\)\.npmrc$",
    r"(^|/|\\)\.pypirc$",
    r"(^|/|\\)\.netrc$",
    r"(^|/|\\)\.git-credentials$",
    r"(^|/|\\)\.docker/config\.json$",
    r"(^|/|\\)\.config/gh/hosts\.yml$",
    r"(^|/|\\)\.git/config$",
    r"(^|/|\\)\.git/hooks(/|\\|$)",
    r"(^|/|\\)\.(bash_history|zsh_history|history)$",
    r"(^|/|\\)(sam|system|security)\.hive$",
    r"(^|/|\\)ntds\.dit$",
    r"(/etc|/private/etc)/(passwd|shadow|master\.passwd|sudoers|hosts)$",
]
