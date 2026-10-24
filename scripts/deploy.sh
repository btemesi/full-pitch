#!/usr/bin/env bash
# Build, push and roll out a new image to Azure Container Apps. Stops at the first failure, so the app is
# never pointed at an image that was not pushed.
# Usage: scripts/deploy.sh v7
set -euo pipefail

TAG="${1:?usage: scripts/deploy.sh <tag>}"
REGISTRY="${REGISTRY:-fullpitchacr6307}"
APP="${APP:-fullpitch}"
RG="${RG:-rg-fullpitch}"
IMAGE="$REGISTRY.azurecr.io/fullpitch:$TAG"

cd "$(dirname "$0")/.."
docker info >/dev/null                      # the Docker daemon must be running
docker build -t "$IMAGE" .
az acr login -n "$REGISTRY" -o none
docker push "$IMAGE"
az acr repository show -n "$REGISTRY" --image "fullpitch:$TAG" -o none   # the image really is in the registry
NEW=$(az containerapp update -n "$APP" -g "$RG" --image "$IMAGE" --query properties.latestRevisionName -o tsv)
echo "rolled out $IMAGE as $NEW"
for _ in $(seq 1 30); do
  [ "$(az containerapp revision show -n "$APP" -g "$RG" --revision "$NEW" --query properties.healthState -o tsv)" = Healthy ] && break
  sleep 10
done
az containerapp revision show -n "$APP" -g "$RG" --revision "$NEW" --query "{revision:name,health:properties.healthState}" -o tsv
# one replica serves the demo: switch off the revisions it replaced
for OLD in $(az containerapp revision list -n "$APP" -g "$RG" --query "[?properties.active && name!='$NEW'].name" -o tsv); do
  az containerapp revision deactivate -n "$APP" -g "$RG" --revision "$OLD" -o none
done
