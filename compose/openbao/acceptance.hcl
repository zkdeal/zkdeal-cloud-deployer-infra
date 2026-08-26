ui = false
api_addr = "http://openbao:8200"

storage "file" {
  path = "/openbao/data"
}

listener "tcp" {
  address     = "0.0.0.0:8200"
  tls_disable = true
}

audit "file" "zkdeal-file" {
  description = "Acceptance audit stream with default HMAC redaction."
  options = {
    file_path = "/var/log/openbao/audit.log"
    log_raw   = "false"
    mode      = "0600"
  }
}
