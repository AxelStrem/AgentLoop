# Running AgentLoop in a container

Each release is also an image, for `linux/amd64` and `linux/arm64`:

```sh
docker pull ghcr.io/axelstrem/agentloop:<version>     # or :latest
```

It runs `agentloop serve` on port 8765, listening on the network, so set `AGENTLOOP_TOKEN` (see
"Reaching the page from another machine" in [install.md](install.md)). Everything AgentLoop keeps
goes in the volume `/data`: settings, logs, the workers' Claude folders, key files, each project's
tools, and the user's home folder (`/data/home`), so a new image keeps it all.

## docker run

```sh
docker run -d --name agentloop -p 8765:8765 \
  -v /path/on/host/agentloop:/data \
  -v /path/on/host/code:/code \
  -e AGENTLOOP_TOKEN=<16+ random characters> \
  -e CLAUDE_CODE_OAUTH_TOKEN=<from claude setup-token> \
  -e GIT_USER_NAME="Your Name" -e GIT_USER_EMAIL=you@example.org \
  ghcr.io/axelstrem/agentloop:latest
```

## Compose

The same, as `compose.yaml`:

```yaml
services:
  agentloop:
    image: ghcr.io/axelstrem/agentloop:latest
    container_name: agentloop
    restart: unless-stopped
    ports:
      - "8765:8765"
    volumes:
      - /path/on/host/agentloop:/data
      - /path/on/host/code:/code
    environment:
      AGENTLOOP_TOKEN: "<16+ random characters>"
      CLAUDE_CODE_OAUTH_TOKEN: "<from claude setup-token>"
      GIT_USER_NAME: "Your Name"
      GIT_USER_EMAIL: "you@example.org"
```

Keep the secrets in an `.env` file beside it (`AGENTLOOP_TOKEN: ${AGENTLOOP_TOKEN}`), not in a file
you commit.

## Unraid

The repository carries a template, [`docker/unraid/agentloop.xml`](../docker/unraid/agentloop.xml),
that turns the variables below into a form:

1. On the **Docker** tab, under **Template repositories** (at the bottom of the page's settings),
   add `https://github.com/AxelStrem/AgentLoop/tree/main/docker/unraid`, and save. Or put the
   file in `/boot/config/plugins/dockerMan/templates-user/` on the server.
2. **Add Container**, and pick **agentloop** from the template list.
3. Fill in the form: the **AGENTLOOP_TOKEN** (16 or more characters, long and random) and the
   Claude sign-in (below) are the ones that matter. `PUID` and `PGID` are already Unraid's `99` and
   `100`. The data folder defaults to `/mnt/user/appdata/agentloop`.
4. For each repository a loop works on, use **Add another Path, Port, Variable...**, choose
   **Path**, and give a container path (`/code/orchard`) and the host folder that holds the clone.
   Name that container path in the loop's places.
5. **Apply**, then open the **WebUI**.

The template is not in Community Applications; adding it by its address is all it needs.

## Variables

| Variable | What it does |
|---|---|
| `AGENTLOOP_TOKEN` | the page's sign-in token. Without it, a new one is printed in the container's log at each start |
| `PUID`, `PGID` | the user and group that serve, the loops and their sessions run as; 99 and 100 (Unraid's) when unset. Never 0: Claude Code refuses to run its sessions as root. Give the ids that own your clones |
| `EXTRA_PACKAGES` | Debian packages a project needs beyond the image's, space-separated, installed at each start |
| `GIT_USER_NAME`, `GIT_USER_EMAIL` | git's name and email for the loops' commits |
| `GIT_TOKEN` | a token for HTTPS remotes, kept in `/data/home/.git-credentials`, readable by the user alone. `GIT_TOKEN_HOST` (`github.com`) and `GIT_TOKEN_USER` (`x-access-token`) say what it is for |
| `CLAUDE_CODE_OAUTH_TOKEN` | Claude's sign-in for every worker with no `signIn`; see "Signing in to Claude" below |

- **Clones** are mounted wherever you like, and a loop's places name them by their paths in the
  container (`/code/orchard`). Nothing under them is chowned. Sessions write as `PUID`, so the
  files they make belong to you. A project's definition may also give a repository an address, and
  **Clone here** on the page clones it into a folder you have mounted.
- **An SSH key** for git goes in `/data/home/.ssh` (the key, and a `known_hosts` beside it), mode
  600, owned by `PUID`.
- **System packages:** the image has git, Node, `claude`, a compiler and linker
  (`build-essential`), `pkg-config`, OpenSSL's headers, `curl`, and the libraries headless Chrome
  needs. A project's setup script installs the rest as the user, into its own folder under `/data`.
- **Started with `--user`**, the image skips all of the above and runs serve as that user.

## Signing in to Claude

In a container nobody runs `claude auth login` at a desktop. There are three ways for a worker to
sign in, and `agentloop accounts list` and the page's **This machine** show which one each worker
uses. They name the variable a secret is in, and never show the secret.

- **A subscription token.** Run `claude setup-token` anywhere claude is installed, signed in to the
  account. It prints a long-lived token. Put the token in an environment variable of the container,
  and name that variable in the worker's `signIn`: `{"token": "CLAUDE_TOKEN_MAIN"}`. Its sessions
  get it as `CLAUDE_CODE_OAUTH_TOKEN`. Two workers naming two variables are two accounts. A
  container that sets `CLAUDE_CODE_OAUTH_TOKEN` itself gives that token to every worker with no
  `signIn`.
- **Logging in inside the container.** Run `docker exec -it <container> agentloop accounts login
  <worker> --run`. claude prints an address: open it in a browser on any machine, sign in, and
  paste the code it shows back into the terminal. No browser is needed in the container. Give the
  worker a `claude` folder under `/data`, such as `/data/claude/main`, so the login is kept on the
  volume and outlasts a new image. Without `--run`, the command only prints what to run.
- **An API key** from the Claude Console, named in the worker's `signIn` as `{"apiKey":
  "ANTHROPIC_KEY_MAIN"}`, reaches its sessions as `ANTHROPIC_API_KEY`. **It is billed per use,
  for every token, not from a subscription.** It has no five-hour or weekly limit, so the usage
  line says "billed per use" and `stopAtWeeklyUse` never stops it. An API error, such as a rate
  limit or a spent balance, ends the session as a failed one. The loop does not wait it out.

A worker whose variable is not set is refused when its loop starts, and the error names the
variable. Nothing of the token or key is written to the settings, the logs or the page.

## What a redeploy does

**A redeploy** stops the container, and every loop with it: the session at work is cut off, its
changes left uncommitted in the clone, as after a kill. The new container's serve
(`agentloop serve --restart-loops`) starts again each loop that was cut off. A loop you had stopped
stays stopped. Each one lets go of the item its last run claimed, and picks from the queue again.
Settings, logins and each project's tools are in `/data`, so they are still there.
