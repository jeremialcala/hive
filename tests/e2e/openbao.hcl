# OpenBao for the end-to-end test: Andvari's role, with TLS and with the certificate of the test's own CA. In memory:
# it lives as long as the test, and conftest.py initializes and unseals it.
storage "inmem" {}

listener "tcp" {
  address       = "0.0.0.0:8200"
  tls_cert_file = "/run/e2e/tls/servidor.crt"
  tls_key_file  = "/run/e2e/tls/servidor.key"
}

disable_mlock = true
api_addr      = "https://openbao:8200"
