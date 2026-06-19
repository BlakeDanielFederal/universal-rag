# Authentication Service

User authentication uses OAuth2 with the authorization-code flow. Access tokens are
short-lived JSON Web Tokens (JWTs) issued by the gateway and expire after 15 minutes;
refresh tokens last 30 days and are rotated on every use.

Service-to-service calls use mutual TLS plus a signed service token. All token
validation happens at the gateway; downstream services trust the forwarded identity
header. Failed logins are rate-limited to five attempts per minute per IP.
