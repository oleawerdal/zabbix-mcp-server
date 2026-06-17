#!/bin/sh
#
# Zabbix MCP Server — container entrypoint
# Copyright (C) 2026 initMAX s.r.o.
#
# Seeds a default config on first run so the server can boot and the admin
# portal comes up even when no config.toml has been provided yet (e.g. a
# fresh named volume on Coolify/Dokploy). When a config already exists
# (bind-mounted or previously seeded), it is left untouched.
#
set -e

CONFIG="/etc/zabbix-mcp/config.toml"
DEFAULT="/opt/zabbix-mcp/default-config.toml"

if [ -d "$CONFIG" ]; then
    echo "ERROR: $CONFIG is a directory, not a file." >&2
    echo "       This usually means a bind mount pointed at a path that did not" >&2
    echo "       exist on the host, so Docker created a directory. Mount an actual" >&2
    echo "       config file there, or use a named volume so the default config" >&2
    echo "       can be seeded automatically." >&2
    exit 1
fi

if [ ! -e "$CONFIG" ]; then
    echo "No config found at $CONFIG — seeding default config." >&2
    echo "Configure your Zabbix server in the admin portal once it is up." >&2
    cp "$DEFAULT" "$CONFIG"
fi

exec /opt/zabbix-mcp/venv/bin/zabbix-mcp-server "$@"
