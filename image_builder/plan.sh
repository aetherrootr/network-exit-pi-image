# Stage order and ownership are declared in image_builder/stages.json, never in this helper.
plan=$(python3 "$project/image_builder/stage_catalog.py" plan --root "$project") || exit 1
mapfile -t BUILD_STAGES <<< "$plan"
unset plan

list_build_stages() {
  local entry id context title description script
  for entry in "${BUILD_STAGES[@]}"; do
    IFS='|' read -r id context title description script <<< "$entry"
    printf '%s [%s] %s\n  %s\n' "$id" "$context" "$title" "$description"
  done
}
