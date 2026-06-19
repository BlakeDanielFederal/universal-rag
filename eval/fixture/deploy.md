# Deployment Runbook

Deployments run through the ZephyrPipeline operator on Kubernetes. Each release is a
blue/green rollout: the new color is brought up, smoke tests run against it, and
traffic is shifted 10% at a time. If error rates exceed 1% during a shift, the
operator automatically rolls back to the previous color.

Database migrations run before the new color receives traffic and must be backward
compatible. Secrets are pulled from the vault at pod startup; nothing is baked into
images. On-call engineers are paged if a rollback occurs.
