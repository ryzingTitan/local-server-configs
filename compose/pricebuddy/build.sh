podman build -f docker/base.dockerfile -t jez500/pricebuddy-base-8.4:latest .
podman build -f docker/tests.dockerfile -t jez500/pricebuddy-tests-8.4:latest .
podman build -f docker/php.dockerfile -t jez500/pricebuddy:latest .