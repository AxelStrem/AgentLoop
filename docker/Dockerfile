# AgentLoop as a container image (item docker-image): the page on the network behind its token,
# loops and their Claude sessions run as an ordinary user, everything kept under the /data volume.
#
# Built from the repository's root, with the release's wheel in dist/ (the release workflow builds
# it first; `pyproject-build` does the same by hand):
#
#     docker build -f docker/Dockerfile -t agentloop .
#
# docs/container.md, says what to give it.

# claude is not in the image (item docker-claude-runtime): the entrypoint installs it as the user into
# the /data volume on the first start, with Anthropic's native installer, and it updates itself
# there. The image has neither node nor npm (item docker-image-size): a project that needs node
# lists it in its Brewfile.
FROM python:3.12-slim-bookworm

# git and ssh for the loops; tini to pass docker's stop on to serve and reap what dies; gosu to run
# serve as the user. The rest is what a project's setup script commonly needs and cannot install
# without root: a compiler and linker, OpenSSL's headers, and the libraries headless Chrome loads.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      git openssh-client tini gosu ca-certificates curl \
      build-essential pkg-config libssl-dev \
      libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libxkbcommon0 libxcomposite1 \
      libxdamage1 libxfixes3 libxrandr2 libgbm1 libasound2 libpango-1.0-0 libcairo2 \
      fonts-liberation \
      procps file unzip \
 && rm -rf /var/lib/apt/lists/*

# Homebrew, the one way a project's tools are installed here (item brew-tools). It lives in a volume
# at its default prefix, where its bottles work; the entrypoint fills an empty one from this copy.
# procps and file above are what Homebrew needs on Linux beyond the compiler, git and curl. The clone
# is shallow, a few MB rather than 140: brew runs from one, and its first `brew update` fetches what
# it needs into the volume, not the image.
RUN git clone --depth 1 --quiet https://github.com/Homebrew/brew /opt/homebrew-seed/Homebrew \
 && mkdir -p /opt/homebrew-seed/bin /home/linuxbrew/.linuxbrew \
 && ln -s ../Homebrew/bin/brew /opt/homebrew-seed/bin/brew

# The release's wheel: the image carries the version it is tagged with.
COPY dist/*.whl /tmp/wheels/
RUN PIP_ROOT_USER_ACTION=ignore pip install --no-cache-dir /tmp/wheels/*.whl && rm -rf /tmp/wheels

COPY docker/entrypoint.sh /usr/local/bin/agentloop-entrypoint
COPY docker/ensure-claude.sh /usr/local/bin/agentloop-ensure-claude
RUN chmod 755 /usr/local/bin/agentloop-entrypoint /usr/local/bin/agentloop-ensure-claude

# Settings, logs, the workers' Claude folders, key files and each project's tools under /data, and
# the user's home there too, so that ~/.claude, ~/.ssh, git's settings and claude itself (in
# ~/.local, where it updates itself) outlast a new image.
ENV AGENTLOOP_HOME=/data \
    HOME=/data/home \
    AGENTLOOP_HOST=0.0.0.0 \
    PUID=99 \
    PGID=100
# The one folder every project's repositories are cloned under (a single mount), what
# `brew shellenv` would set, claude's folder in the volume, and brew's own update at most once a
# day rather than before every install.
ENV AGENTLOOP_CODE=/code \
    AGENTLOOP_BREW=1 \
    HOMEBREW_PREFIX=/home/linuxbrew/.linuxbrew \
    HOMEBREW_CELLAR=/home/linuxbrew/.linuxbrew/Cellar \
    HOMEBREW_REPOSITORY=/home/linuxbrew/.linuxbrew/Homebrew \
    HOMEBREW_AUTO_UPDATE_SECS=86400 \
    HOMEBREW_NO_ANALYTICS=1 \
    HOMEBREW_NO_ENV_HINTS=1 \
    PATH=/home/linuxbrew/.linuxbrew/bin:/home/linuxbrew/.linuxbrew/sbin:/data/home/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
VOLUME /data
EXPOSE 8765

ENTRYPOINT ["tini", "--", "/usr/local/bin/agentloop-entrypoint"]
CMD ["agentloop", "serve", "--restart-loops"]
