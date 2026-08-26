# Development mode does not activate declarative audit stanzas. This isolated
# local profile temporarily permits the init container to create one fixed file
# device. Production uses production-audit.example.hcl and leaves this false.
unsafe_allow_api_audit_creation = true
