# GitHub access on this PC

This repository belongs to **djienne**. The working SSH key on this PC is
`~/.ssh/id_rsa_reflechir`, verified on 2026-09-30. Git's `core.sshCommand` selects
this key with `IdentitiesOnly=yes`.

Push URL: `git@github.com:djienne/COPY_WALLET_HYPERLIQUID.git`.

Verify with `ssh -i ~/.ssh/id_rsa_reflechir -o IdentitiesOnly=yes -T git@github.com`:
the greeting must say
`Hi djienne!` (GitHub's successful SSH identity check exits with status 1).
The `github-djienne` SSH alias currently selects `id_rsa2`, which fails authentication;
do not rely on its name. The default HTTPS Git/gh account is `davsar89` and cannot push here.
Do not print or commit private keys, tokens, or `hyperliquid.env`.

Preserve the local deployment and trading history when updating from upstream;
read `README.md` and the parent workspace's `AGENTS.md` first.
