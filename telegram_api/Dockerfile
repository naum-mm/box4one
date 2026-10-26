# Official TDLib Bot API source, pinned to an immutable upstream commit.
FROM python:3.13-slim-bookworm@sha256:e32be0e55814a08977501ca863271d9bd02443710db8f2afaf8372e2c4508530 AS build
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates cmake g++ gperf libssl-dev zlib1g-dev make && rm -rf /var/lib/apt/lists/*
WORKDIR /src
RUN git init && git remote add origin https://github.com/tdlib/telegram-bot-api.git && git fetch --depth=1 origin e3e9dd8e5b3d7ab8537cd5a10dc31d5ffa8f82d1 && git checkout --detach FETCH_HEAD && git submodule update --init --recursive --depth=1
RUN cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DCMAKE_CXX_FLAGS_RELEASE="-O2 -DNDEBUG" -DCMAKE_INSTALL_PREFIX=/out && cmake --build build --target install --parallel 1
FROM python:3.13-slim-bookworm@sha256:e32be0e55814a08977501ca863271d9bd02443710db8f2afaf8372e2c4508530
RUN apt-get update && apt-get install -y --no-install-recommends libssl3 zlib1g libstdc++6 libatomic1 ca-certificates && rm -rf /var/lib/apt/lists/*
COPY --from=build /out/bin/telegram-bot-api /usr/local/bin/telegram-bot-api
COPY health.py /app/health.py
USER 65532:65532
WORKDIR /var/lib/telegram-bot-api
ENTRYPOINT ["telegram-bot-api"]
CMD ["--local", "--http-port=8081", "--dir=/var/lib/telegram-bot-api", "--temp-dir=/var/lib/telegram-bot-api/tmp", "--verbosity=0"]
