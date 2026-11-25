# Running AgentLoop in a container

Each release is also an image, for `linux/amd64` (not ARM: an ARM image would need building on an
ARM runner of its own):

```sh
docker pull ghcr.io/axelstrem/agentloop:<version>     # or :latest
```

It runs `agentloop serve` on port 8765, listening on the network, so set `AGENTLOOP_TOKEN` (see
"Reaching the page from another machine" in [install.md](install.md)). Everything AgentLoop keeps
goes in the volume `/data`: settings, logs, the workers' Claude folders, key files, each project's
tools, and the user's home folder (`/data/home`), so a new image keeps it all. Homebrew, and every
tool the projects' Brewfiles install, goes in a second volume, `/home/linuxbrew/.linuxbrew`,
shared by all projects. It is a volume of its own because Homebrew's bottles work only at that path.

## docker run

```sh
docker run -d --name agentloop -p 8765:8765 \
  -v /path/on/host/agentloop:/data \
  -v /path/on/host/agentloop-brew:/home/linuxbrew/.linuxbrew \
  -v /path/on/host/code:/code \
  -e AGENTLOOP_TOKEN=<16+ random characters> \
  -e CLAUDE_CODE_OAUTH_TOKEN=<from claude setup-token> \
  -e GIT_USER_NAME="Your Name" -e GIT_USER_EMAIL=you@example.org \
  ghcr.io/axelstrem/agentloop:latest
```

## Compose

The same is [`docker/compose.yaml`](../docker/compose.yaml), with Unraid's paths: copy it, change
the paths and git's name, and put the secrets in an `.env` file beside it, as its header says.
`docker compose up -d` in that folder starts it.

## Unraid

The repository carries a template, [`docker/unraid/agentloop.xml`](../docker/unraid/agentloop.xml),
that turns the variables below into a form:

1. On the **Docker** tab, under **Template repositories** (at the bottom of the page's settings),
   add `https://github.com/AxelStrem/AgentLoop/tree/main/docker/unraid`, and save. Or put the
   file in `/boot/config/plugins/dockerMan/templates-user/` on the server.
2. **Add Container**, and pick **agentloop** from the template list.
3. Fill in the form: the **AGENTLOOP_TOKEN** (16 or more characters, long and random) and the
   Claude sign-in (below) are the ones that matter. `PUID` and `PGID` are already Unraid's `99` and
   `100`. The data folder defaults to `/mnt/user/appdata/agentloop`, and Homebrew's to
   `/mnt/user/appdata/agentloop-brew`. Homebrew is many small files and symlinks: with appdata on a
   cache pool, give it `/mnt/cache/appdata/agentloop-brew`, past Unraid's slower user-share layer.
   The **Code** folder (`/mnt/user/code`, `/code` inside) holds every repository, each in a folder
   of its name.
4. **Apply**, then open the **WebUI**.

The template is not in Community Applications; adding it by its address is all it needs.

## Variables

| Variable | What it does |
|---|---|
| `AGENTLOOP_TOKEN` | the page's sign-in token. Without it, a new one is printed in the container's log at each start |
| `PUID`, `PGID` | the user and group that serve, the loops and their sessions run as; 99 and 100 (Unraid's) when unset. Never 0: Claude Code refuses to run its sessions as root. Give the ids that own your clones |
| `EXTRA_PACKAGES` | Debian packages a project needs beyond the image's, space-separated, installed at each start. A Brewfile is the better home for a tool |
| `AGENTLOOP_CODE` | `/code` in the image: the one folder every repository is offered to be cloned under, as `/code/<repo>`, for every project. Root's, as a new mount is, it is given to `PUID`; nothing inside it is |
| `AGENTLOOP_BREW` | `1` in the image: projects' tools come from their Brewfiles ([settings.md](settings.md), "Its Brewfile"). Anything else turns that off |
| `GIT_USER_NAME`, `GIT_USER_EMAIL` | git's name and email for the loops' commits |
| `GIT_TOKEN` | a token for HTTPS remotes, kept in `/data/home/.git-credentials`, readable by the user alone. `GIT_TOKEN_HOST` (`github.com`) and `GIT_TOKEN_USER` (`x-access-token`) say what it is for |
| `CLAUDE_CODE_OAUTH_TOKEN` | Claude's sign-in for every worker with no `signIn`; see "Signing in to Claude" below |

- **Clones** all go in the one `/code` mount. When a project's definition gives a repository an
  address, **Add from a tracker** offers `/code/<repo>` and **Clone here** clones it there; a clone
  already there of the same address is used as it is. A repository with no address is given its
  folder by hand, under `/code` too. Nothing under it is chowned, and sessions write as `PUID`, so
  the files they make belong to you. Two projects with a repository of the same name and different
  addresses need one of the two given another folder by hand.
- **An SSH key** for git goes in `/data/home/.ssh`, mode 600, owned by `PUID`. A GitHub address
  is cloned, fetched and pushed in the form, HTTPS or SSH, the container signs in with: an SSH key,
  `GIT_TOKEN`, or both ([settings.md](settings.md), "HTTPS or SSH").
- **claude** is not in the image. The first start installs it as the user into the volume, at
  `/data/home/.local/bin/claude` (its versions in `/data/home/.local/share/claude`), with
  Anthropic's native installer, and serve starts once it is done. That needs the network and
  downloads about 200 MB, once. claude then updates itself there, so it stays current without a
  new image, and a new image keeps it. A start that finds it there and running installs nothing.
  A failed install is tried three times, then the log says so and serve starts anyway. Sessions
  then fail with "claude not found", and the next start tries again. To install it afresh, delete
  those two folders and restart the container. Started with `--user`, the image installs it in
  that user's `$HOME/.local`, which is on PATH only when `HOME` is `/data/home`, as the image sets
  it.
- **System packages:** the image has git, a compiler and linker (`build-essential`),
  `pkg-config`, OpenSSL's headers, `curl`, `unzip`, and the libraries headless Chrome needs. It has no Node or npm: a project that needs them lists
  `brew "node"` in its Brewfile.
- **A project's tools** come from the `Brewfile` in its first repository, installed as the user
  into the Homebrew volume before each session; a session adds to it itself (see
  [settings.md](settings.md), "Its Brewfile"). The first start fills an empty volume from the
  image's copy of Homebrew, a shallow clone that brew's first update fills out in the volume. The
  Brewfile is installed before the project's setup script runs, so the script can use its tools.
  Brew's `bin` comes first on PATH, so a brewed `node` or `python3` wins for sessions. Without that
  volume mounted, the log says so at each start, and what brew installed is lost when the container
  is recreated.
- **Started with `--user`**, the image skips all of the above but the claude install, and runs
  serve as that user.

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
  line says "billed per use" and the worker's limits never pause it. An API error, such as a rate
  limit or a spent balance, ends the session as a failed one. The loop does not wait it out.

A worker whose variable is not set is refused when its loop starts, and the error names the
variable. Nothing of the token or key is written to the settings, the logs or the page.

## What a redeploy does

**A redeploy** stops the container, and every loop with it: the session at work is cut off, its
changes left uncommitted in the clone, as after a kill. The new container's serve starts again
each loop that was cut off, as `agentloop serve` does on every system (see "When serve starts" in
[settings.md](settings.md)); the image still passes `--restart-loops`, which asks for what is now
the default. A loop you had stopped stays stopped. Each one lets go of the item its last run claimed, and picks from the queue again.
Settings, logins and each project's tools are in `/data` and the Homebrew volume, so they are
still there. Images up to 0.1.9 carried Node and npm and later ones do not (item `docker-image-size`), so
a project whose sessions or setup script used the image's `node` adds `brew "node"` to its
Brewfile, and the loop installs it before the next session.
