# Merge into the operator-owned OpenBao HA configuration. Declarative audit
# devices are the production-safe boundary; API creation remains disabled.
audit "file" "zkdeal-file" {
  description = "Persistent zkdeal security audit stream; values are HMACed."
  options = {
    file_path = "/var/log/openbao/audit.log"
    log_raw   = "false"
    mode      = "0600"
  }
}
