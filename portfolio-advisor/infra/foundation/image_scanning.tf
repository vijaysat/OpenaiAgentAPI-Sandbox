# Hosted artifact registration requires a scan report for the exact image.
# The service can pull only this private repository, with no write privileges.
resource "oci_identity_policy" "image_scanner" {
  compartment_id = local.application_compartment_id
  name           = "${var.prefix}-image-scanner"
  description    = "Scan only the dedicated Portfolio Advisor image repository"
  statements = [
    # Collection lookup has no individual target.repo.name to evaluate.
    "Allow service vulnerability-scanning-service to inspect repos in compartment id ${local.application_compartment_id}",
    "Allow service vulnerability-scanning-service to read repos in compartment id ${local.application_compartment_id} where target.repo.name = '${oci_artifacts_container_repository.this.display_name}'"
  ]
}

resource "oci_identity_policy" "scanner_compartment_lookup" {
  compartment_id = var.compartment_id
  name           = "${var.prefix}-scanner-compartment"
  description    = "Read only the dedicated child compartment metadata"
  statements = [
    "Allow service vulnerability-scanning-service to read compartments in compartment id ${var.compartment_id} where target.compartment.id = '${local.application_compartment_id}'"
  ]
}

resource "oci_vulnerability_scanning_container_scan_recipe" "image" {
  compartment_id = local.application_compartment_id
  display_name   = "${var.prefix}-images"
  image_count    = 2
  scan_settings {
    scan_level = "STANDARD"
  }
  freeform_tags = local.tags
}

resource "oci_vulnerability_scanning_container_scan_target" "image" {
  compartment_id           = local.application_compartment_id
  display_name             = "${var.prefix}-images"
  description              = "Dedicated application images only"
  container_scan_recipe_id = oci_vulnerability_scanning_container_scan_recipe.image.id
  target_registry {
    compartment_id = local.application_compartment_id
    type           = "OCIR"
    repositories   = [oci_artifacts_container_repository.this.display_name]
  }
  freeform_tags = local.tags
  depends_on    = [oci_identity_policy.image_scanner, oci_identity_policy.scanner_compartment_lookup]
}
