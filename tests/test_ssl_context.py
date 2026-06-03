#
# Zabbix MCP Server
# Copyright (C) 2026 initMAX s.r.o.
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU Affero General Public License as published by the Free
# Software Foundation, version 3.
#

"""Issue #51: ``verify_ssl=false`` must also enable unsafe legacy
renegotiation so RHEL 9 (OpenSSL 3.0) can talk to old Zabbix HTTPS
frontends that need legacy TLS renegotiation."""

from __future__ import annotations

import ssl
import unittest

from zabbix_mcp.client import _OP_LEGACY_SERVER_CONNECT, _build_ssl_context


class TestSSLContextBuilder(unittest.TestCase):

    def test_verify_off_disables_certs_and_enables_legacy_renegotiation(self):
        ctx = _build_ssl_context(verify_ssl=False)
        self.assertFalse(ctx.check_hostname)
        self.assertEqual(ctx.verify_mode, ssl.CERT_NONE)
        self.assertTrue(ctx.options & _OP_LEGACY_SERVER_CONNECT)

    def test_verify_on_keeps_default_strict_settings(self):
        ctx = _build_ssl_context(verify_ssl=True)
        self.assertTrue(ctx.check_hostname)
        self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)
        self.assertFalse(ctx.options & _OP_LEGACY_SERVER_CONNECT)


if __name__ == "__main__":
    unittest.main()
