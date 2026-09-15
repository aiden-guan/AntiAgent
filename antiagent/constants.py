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
}

# Safe git subcommands
SAFE_GIT_SUBCOMMANDS = {
    "status",
    "diff",
    "log",
    "show",
    "branch",
    "tag",
    "rev-parse",
    "remote",
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
    r"git\s+branch\s+-[a-zA-Z]*D",
    r"git\s+checkout\s+-[a-zA-Z]*f",
    r"git\s+restore\s+(\.|--staged\s+\.)",
    r"git\s+stash\s+drop",
    r"git\s+stash\s+clear",
]

# Patterns that are immediately blocked without prompting (Tier 1 Hard Block)
HARD_DENY_PATTERNS = [
    # System wiping / destructive deletion (with or without sudo)
    r"(?:sudo\s+)?rm\s+(-[a-zA-Z]*r[a-zA-Z]*f|--recursive\s+--force)\s+(/\s*$|/\*|/etc|/usr|/bin|/sbin|/var|/System|/Library|~/?$)",
    # Disk formatting / raw device overwrites (with or without sudo)
    r"\bmkfs\b",
    r"\bdd\s+if=.*of=(/dev/sd[a-z]|/dev/nvme[0-9]|/dev/disk[0-9])",
    # Fork bombs
    r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:",
    # Piping untrusted network scripts directly to shell
    r"(curl|wget|fetch|http)\s+.*\|\s*(sh|bash|zsh|python[0-9]?|ruby|perl)",
    # SSH and cloud credential exfiltration
    r"(~?/\.ssh/id_[a-zA-Z0-9_]+|\.ssh/id_[a-zA-Z0-9_]+|\.aws/credentials|\.kube/config)",
    # Shadow / password file dump
    r"(?:sudo\s+)?cat\s+.*(/etc/shadow|/etc/master\.passwd)",
    # Tampering with sudoers configuration
    r"(>>|>|visudo|tee)\s+.*(/etc/sudoers)",
]

# Sensitive file and directory patterns
SENSITIVE_TARGETS = [
    r"\.env(\.[a-zA-Z0-9_.-]+)?$",
    r"\.ssh/",
    r"\.aws/",
    r"\.kube/",
    r".*\.pem$",
    r".*\.key$",
    r".*\.pfx$",
    r".*\.p12$",
    r"id_rsa",
    r"id_ed25519",
    r"id_ecdsa",
]
