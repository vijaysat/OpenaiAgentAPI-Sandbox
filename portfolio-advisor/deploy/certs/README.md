The hosted rollout writes the dedicated PostgreSQL service's public CA certificate
to `postgres-ca.pem` before packaging the build source. No private key belongs here.
The runtime connection URL uses `sslmode=verify-full` and `/app/certs/postgres-ca.pem`.
