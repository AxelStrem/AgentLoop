# AgentLoop as a container image (item docker-image): the page on the network behind its token,
# loops and their Claude sessions run as an ordinary user, everything kept under the /data volume.
#
# Built from the repository's root, with the release's wheel in dist/ (the release workflow builds
# it first; `pyproject-build` does the same by hand):
#
#     docker build -f docker/Dockerfile -t agentloop .
#
# docs/container.md, says what to give it.

# Node comes from its own image, of the same Debian release as Python's, rather than from a script
# piped to a shell.
FROM node:22-bookworm-slim AS node

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
 && rm -rf /var/lib/apt/lists/*

COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
 && npm install -g @anthropic-ai/claude-code \
 && npm cache clean --force

# The release's wheel: the image carries the version it is tagged with.
COPY dist/*.whl /tmp/wheels/
RUN PIP_ROOT_USER_ACTION=ignore pip install --no-cache-dir /tmp/wheels/*.whl && rm -rf /tmp/wheels

COPY docker/entrypoint.sh /usr/local/bin/agentloop-entrypoint
RUN chmod 755 /usr/local/bin/agentloop-entrypoint

# Settings, logs, the workers' Claude folders, key files and each project's tools under /data, and
# the user's home there too, so that ~/.claude, ~/.ssh and git's settings outlast a new image.
# claude updates itself where it was installed, which the user cannot write: a new image brings a
# new claude instead.
ENV AGENTLOOP_HOME=/data \
    HOME=/data/home \
    AGENTLOOP_HOST=0.0.0.0 \
    DISABLE_AUTOUPDATER=1 \
    PUID=99 \
    PGID=100
VOLUME /data
EXPOSE 8765

ENTRYPOINT ["tini", "--", "/usr/local/bin/agentloop-entrypoint"]
CMD ["agentloop", "serve", "--restart-loops"]
