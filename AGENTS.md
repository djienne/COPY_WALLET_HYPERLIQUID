# GitHub access on this PC

This repository belongs to **djienne**. Use the SSH host alias `github-djienne`
from `~/.ssh/config`; it selects `~/.ssh/id_rsa2` for that GitHub account.

Push URL: `git@github-djienne:djienne/COPY_WALLET_HYPERLIQUID.git`.

Verify the identity with `ssh -T git@github-djienne`: the greeting must say
`Hi djienne!` (GitHub's successful SSH identity check exits with status 1).
The default HTTPS Git/gh account is `davsar89` and cannot push to this repository.
Do not print or commit private keys, tokens, or `hyperliquid.env`.

Preserve the local deployment and trading history when updating from upstream;
read `README.md` and the parent workspace's `AGENTS.md` first.
