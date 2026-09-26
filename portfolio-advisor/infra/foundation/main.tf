terraform {
  required_version = ">= 1.5.0"
  required_providers {
    oci = {
      source = "oracle/oci", version = "= 8.18.0"
    }
  }
}
provider "oci" {
  region = var.region
}
variable "region" {
  type = string
}
variable "tenancy_id" {
  type = string
}
variable "compartment_id" {
  type        = string
  description = "Parent compartment containing the already-created dedicated Vault."
}
variable "existing_vault_id" { type = string }
variable "existing_key_id" { type = string }
resource "oci_identity_compartment" "this" {
  compartment_id = var.compartment_id
  name           = var.prefix
  description    = "Dedicated Portfolio Advisor hosted application, build and sandbox resources"
  enable_delete  = false
  freeform_tags  = local.tags
  lifecycle { prevent_destroy = true }
}

variable "prefix" {
  type    = string
  default = "portfolio-advisor"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,30}$", var.prefix))
    error_message = "Use a unique lowercase resource prefix."
  }
}
variable "vcn_cidr" {
  type    = string
  default = "10.94.0.0/16"
}
variable "runtime_ids" {
  type        = list(string)
  default     = []
  description = "Exact hosted application (image pull) and hosted deployment (container resource principal) OCIDs."
  validation {
    condition     = alltrue([for id in var.runtime_ids : can(regex("^ocid1.generativeaihosted(application|deployment)\\.[a-z0-9.-]+$", id))])
    error_message = "Runtime identities must be exact hosted application or deployment OCIDs."
  }
}
variable "secret_ids" {
  type = map(string)
  default = {
  }
  validation {
    condition     = length(var.secret_ids) == 0 || (length(var.secret_ids) == 5 && alltrue([for id in values(var.secret_ids) : startswith(id, "ocid1.vaultsecret.")]))
    error_message = "Supply exactly the five dedicated application secret OCIDs after Vault seeding."
  }
}
variable "postgres_admin_secret_id" {
  type    = string
  default = ""
}
variable "postgres_ocpus" {
  type    = number
  default = 2
}
variable "postgres_memory_gb" {
  type    = number
  default = 32
}
variable "source_branch" {
  type    = string
  default = "refs/heads/main"
}
variable "registry_publish_until" {
  type        = string
  default     = ""
  description = "Explicitly approved, fixed UTC expiry for temporary compartment registry management. Empty disables the grant. Never auto-renew."
  validation {
    condition     = var.registry_publish_until == "" || can(regex("^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$", var.registry_publish_until))
    error_message = "Use an approved fixed UTC timestamp or leave this empty."
  }
}
locals {
  application_compartment_id = oci_identity_compartment.this.id
  tags = {
    application = "portfolio-advisor", managed_by = "portfolio-rollout"
  }
  subnets = {
    application = 1, database = 2, build = 3
  }
}
data "oci_objectstorage_namespace" "this" {
  compartment_id = var.tenancy_id
}
data "oci_core_services" "all" {
  filter {
    name   = "name"
    values = ["All .* Services In Oracle Services Network"]
    regex  = true
  }
}
resource "oci_core_vcn" "this" {
  compartment_id = local.application_compartment_id
  cidr_blocks    = [var.vcn_cidr]
  display_name   = var.prefix
  dns_label      = "portfolio"
  freeform_tags  = local.tags
}
resource "oci_core_nat_gateway" "this" {
  compartment_id = local.application_compartment_id
  vcn_id         = oci_core_vcn.this.id
  display_name   = "${var.prefix}-nat"
}
resource "oci_core_service_gateway" "this" {
  compartment_id = local.application_compartment_id
  vcn_id         = oci_core_vcn.this.id
  services {
    service_id = data.oci_core_services.all.services[0].id
  }
}
resource "oci_core_route_table" "private" {
  compartment_id = local.application_compartment_id
  vcn_id         = oci_core_vcn.this.id
  route_rules {
    destination       = "0.0.0.0/0"
    network_entity_id = oci_core_nat_gateway.this.id
  }
  route_rules {
    destination       = data.oci_core_services.all.services[0].cidr_block
    destination_type  = "SERVICE_CIDR_BLOCK"
    network_entity_id = oci_core_service_gateway.this.id
  }
}
resource "oci_core_security_list" "empty" {
  compartment_id = local.application_compartment_id
  vcn_id         = oci_core_vcn.this.id
  display_name   = "${var.prefix}-nsg-only"
}
resource "oci_core_subnet" "private" {
  for_each                   = local.subnets
  compartment_id             = local.application_compartment_id
  vcn_id                     = oci_core_vcn.this.id
  cidr_block                 = cidrsubnet(var.vcn_cidr, 8, each.value)
  display_name               = "${var.prefix}-${each.key}"
  dns_label                  = each.key
  prohibit_public_ip_on_vnic = true
  route_table_id             = oci_core_route_table.private.id
  security_list_ids          = [oci_core_security_list.empty.id]
}
resource "oci_core_network_security_group" "this" {
  for_each       = local.subnets
  compartment_id = local.application_compartment_id
  vcn_id         = oci_core_vcn.this.id
  display_name   = "${var.prefix}-${each.key}"
}
resource "oci_core_network_security_group_security_rule" "https" {
  for_each                  = toset(["application", "build"])
  network_security_group_id = oci_core_network_security_group.this[each.key].id
  direction                 = "EGRESS"
  protocol                  = "6"
  destination               = "0.0.0.0/0"
  tcp_options {
    destination_port_range {
      min = 443
      max = 443
    }
  }
}
resource "oci_core_network_security_group_security_rule" "dns" {
  for_each                  = local.subnets
  network_security_group_id = oci_core_network_security_group.this[each.key].id
  direction                 = "EGRESS"
  protocol                  = "17"
  destination               = "169.254.169.254/32"
  udp_options {
    destination_port_range {
      min = 53
      max = 53
    }
  }
}
resource "oci_core_network_security_group_security_rule" "postgres_in" {
  for_each                  = toset(["application", "build"])
  network_security_group_id = oci_core_network_security_group.this["database"].id
  direction                 = "INGRESS"
  protocol                  = "6"
  source_type               = "NETWORK_SECURITY_GROUP"
  source                    = oci_core_network_security_group.this[each.key].id
  tcp_options {
    destination_port_range {
      min = 5432
      max = 5432
    }
  }
}
resource "oci_core_network_security_group_security_rule" "postgres_out" {
  for_each                  = toset(["application", "build"])
  network_security_group_id = oci_core_network_security_group.this[each.key].id
  direction                 = "EGRESS"
  protocol                  = "6"
  destination_type          = "NETWORK_SECURITY_GROUP"
  destination               = oci_core_network_security_group.this["database"].id
  tcp_options {
    destination_port_range {
      min = 5432
      max = 5432
    }
  }
}
resource "oci_objectstorage_bucket" "artifacts" {
  compartment_id = local.application_compartment_id
  namespace      = data.oci_objectstorage_namespace.this.namespace
  name           = "${var.prefix}-private-artifacts"
  access_type    = "NoPublicAccess"
  versioning     = "Disabled"
  freeform_tags  = local.tags
  lifecycle {
    prevent_destroy = true
  }
}
resource "oci_objectstorage_bucket" "build" {
  compartment_id = local.application_compartment_id
  namespace      = data.oci_objectstorage_namespace.this.namespace
  name           = "${var.prefix}-build-inputs"
  access_type    = "NoPublicAccess"
  versioning     = "Enabled"
  freeform_tags  = local.tags
  lifecycle {
    prevent_destroy = true
  }
}
# Reuse the dedicated Vault created by the journaled bootstrap; no secret content in state.
data "oci_kms_vault" "existing" { vault_id = var.existing_vault_id }
data "oci_kms_key" "existing" {
  key_id              = var.existing_key_id
  management_endpoint = data.oci_kms_vault.existing.management_endpoint
}
# Secrets are created by the journaled CLI and NEVER passed through Terraform.
resource "oci_psql_db_system" "this" {
  count                       = var.postgres_admin_secret_id == "" ? 0 : 1
  compartment_id              = local.application_compartment_id
  display_name                = "${var.prefix}-postgres"
  db_version                  = "16"
  shape                       = "PostgreSQL.VM.Standard.E5.Flex"
  instance_count              = 1
  instance_ocpu_count         = var.postgres_ocpus
  instance_memory_size_in_gbs = var.postgres_memory_gb
  system_type                 = "OCI_OPTIMIZED_STORAGE"
  credentials {
    username = "portfolio_admin"
    password_details {
      password_type  = "VAULT_SECRET"
      secret_id      = var.postgres_admin_secret_id
      secret_version = "1"
    }
  }
  network_details {
    subnet_id = oci_core_subnet.private["database"].id
    nsg_ids   = [oci_core_network_security_group.this["database"].id]
  }
  storage_details {
    system_type           = "OCI_OPTIMIZED_STORAGE"
    is_regionally_durable = true
  }
  management_policy {
    maintenance_window_start = "SUN 04:00"
    backup_policy {
      kind           = "DAILY"
      backup_start   = "03:00"
      retention_days = 7
    }
  }
  freeform_tags = local.tags
  lifecycle {
    prevent_destroy = true
  }
}
resource "oci_artifacts_container_repository" "this" {
  compartment_id = local.application_compartment_id
  display_name   = var.prefix
  is_public      = false
  # This region rejects isImmutable. Rollout uses unique tags and verifies digests.
}
resource "oci_ons_notification_topic" "build" {
  compartment_id = local.application_compartment_id
  name           = "${var.prefix}-build"
}
resource "oci_devops_project" "this" {
  compartment_id = local.application_compartment_id
  name           = var.prefix
  notification_config {
    topic_id = oci_ons_notification_topic.build.id
  }
}
resource "oci_devops_repository" "this" {
  project_id      = oci_devops_project.this.id
  name            = var.prefix
  repository_type = "HOSTED"
  default_branch  = var.source_branch
}
resource "oci_devops_build_pipeline" "this" {
  project_id   = oci_devops_project.this.id
  display_name = "${var.prefix}-build"
  description  = "Build and publish the dedicated Portfolio Advisor application"
  build_pipeline_parameters {
    dynamic "items" {
      for_each = toset(["APP_BASE_PATH", "OCI_NAMESPACE", "BUILD_INPUT_BUCKET", "PREVIEW_WHEEL_OBJECT", "PREVIEW_WHEEL_SHA256", "IMAGE_TAG"])
      content {
        name          = items.value
        default_value = "UNSET"
        description   = "Required rollout value for ${items.value}"
      }
    }
  }
}
resource "oci_devops_build_pipeline_stage" "build" {
  build_pipeline_id         = oci_devops_build_pipeline.this.id
  build_pipeline_stage_type = "BUILD"
  display_name              = "Build and validate Portfolio Advisor"
  description               = "Build the reviewed source with the pinned preview SDK"
  build_spec_file           = "portfolio-advisor/deploy/build_spec.yaml"
  image                     = "OL8_X86_64_STANDARD_10"
  primary_build_source      = "portfolio"
  build_source_collection {
    items {
      name            = "portfolio"
      connection_type = "DEVOPS_CODE_REPOSITORY"
      repository_id   = oci_devops_repository.this.id
      repository_url  = oci_devops_repository.this.http_url
      branch          = trimprefix(var.source_branch, "refs/heads/")
    }
  }
  private_access_config {
    network_channel_type = "SERVICE_VNIC_CHANNEL"
    subnet_id            = oci_core_subnet.private["build"].id
    nsg_ids              = [oci_core_network_security_group.this["build"].id]
  }
  build_pipeline_stage_predecessor_collection {
    items {
      id = oci_devops_build_pipeline.this.id
    }
  }
}
resource "oci_identity_dynamic_group" "runtime" {
  count          = length(var.runtime_ids) > 0 ? 1 : 0
  compartment_id = var.tenancy_id
  name           = "${var.prefix}-runtime"
  description    = "Only the exact Portfolio Advisor hosted application and deployment resource IDs"
  matching_rule  = "ANY {${join(", ", [for id in var.runtime_ids : "resource.id = '${id}'"])}}"
}
resource "oci_identity_policy" "runtime" {
  count          = length(var.runtime_ids) > 0 && length(var.secret_ids) == 5 ? 1 : 0
  compartment_id = var.compartment_id
  name           = "${var.prefix}-runtime"
  description    = "Private artifacts, sandbox lifecycle, and the exact five Vault secrets"
  # Direct exact-principal conditions avoid dynamic-group membership propagation
  # during first deployment. Every statement remains restricted to a known OCID.
  statements = flatten([for runtime_id in var.runtime_ids : concat([
    "Allow any-user to manage objects in compartment id ${local.application_compartment_id} where ALL {request.principal.id = '${runtime_id}', target.bucket.name = '${oci_objectstorage_bucket.artifacts.name}'}",
    "Allow any-user to read buckets in compartment id ${local.application_compartment_id} where ALL {request.principal.id = '${runtime_id}', target.bucket.name = '${oci_objectstorage_bucket.artifacts.name}'}",
    "Allow any-user to manage generative-ai-family in compartment id ${local.application_compartment_id} where request.principal.id = '${runtime_id}'",
    "Allow any-user to read repos in compartment id ${local.application_compartment_id} where ALL {request.principal.id = '${runtime_id}', target.repo.name = '${oci_artifacts_container_repository.this.display_name}'}"
    ], [for id in values(var.secret_ids) : "Allow any-user to read secret-bundles in compartment id ${var.compartment_id} where ALL {request.principal.id = '${runtime_id}', target.secret.id = '${id}'}"],
    startswith(runtime_id, "ocid1.generativeaihostedapplication.") ? [
      "Allow any-user to read container-scan-results in compartment id ${local.application_compartment_id} where request.principal.id = '${runtime_id}'"
  ] : [])])
}
resource "oci_identity_dynamic_group" "build" {
  compartment_id = var.tenancy_id
  name           = "${var.prefix}-build"
  description    = "Only the dedicated build pipeline"
  matching_rule  = "ALL {resource.type = 'devopsbuildpipeline', resource.id = '${oci_devops_build_pipeline.this.id}'}"
}
resource "oci_identity_policy" "build" {
  compartment_id = local.application_compartment_id
  name           = "${var.prefix}-build"
  description    = "Build source, private wheel inputs, and OCIR publishing; no application credentials"
  statements = concat([
    "Allow dynamic-group id ${oci_identity_dynamic_group.build.id} to manage devops-family in compartment id ${local.application_compartment_id}",
    # Collection metadata lookup has no individual target.repo.name to evaluate.
    "Allow dynamic-group Default/${oci_identity_dynamic_group.build.name} to inspect repos in compartment id ${local.application_compartment_id}",
    "Allow dynamic-group id ${oci_identity_dynamic_group.build.id} to read objects in compartment id ${local.application_compartment_id} where target.bucket.name = '${oci_objectstorage_bucket.build.name}'",
    "Allow dynamic-group id ${oci_identity_dynamic_group.build.id} to use virtual-network-family in compartment id ${local.application_compartment_id}",
    "Allow dynamic-group id ${oci_identity_dynamic_group.build.id} to use ons-topics in compartment id ${local.application_compartment_id}"
    ], [for permission in ["REPOSITORY_READ", "REPOSITORY_UPDATE"] :
    # Flat conditions preserve the exact repository and the original push permissions.
    "Allow dynamic-group Default/${oci_identity_dynamic_group.build.name} to manage repos in compartment id ${local.application_compartment_id} where all {target.repo.name = '${oci_artifacts_container_repository.this.display_name}', request.permission = '${permission}'}"
    ], var.registry_publish_until == "" ? [] : [
    "Allow dynamic-group Default/${oci_identity_dynamic_group.build.name} to manage repos in compartment id ${local.application_compartment_id} where request.utc-timestamp before '${var.registry_publish_until}'"
  ])
}
output "rollout" {
  value = {
    region                = var.region
    compartment_id        = local.application_compartment_id
    vault_compartment_id  = var.compartment_id
    namespace             = data.oci_objectstorage_namespace.this.namespace
    bucket                = oci_objectstorage_bucket.artifacts.name
    build_bucket          = oci_objectstorage_bucket.build.name
    vault_id              = data.oci_kms_vault.existing.id
    key_id                = data.oci_kms_key.existing.id
    application_subnet_id = oci_core_subnet.private["application"].id
    application_nsg_ids   = [oci_core_network_security_group.this["application"].id]
    database_id           = try(oci_psql_db_system.this[0].id, null)
    repository_id         = oci_artifacts_container_repository.this.id
    repository_name       = var.prefix
    registry_host         = var.registry_host
    source_repository_url = oci_devops_repository.this.http_url
    source_repository_id  = oci_devops_repository.this.id
    build_subnet_id       = oci_core_subnet.private["build"].id
    build_nsg_id          = oci_core_network_security_group.this["build"].id
    build_pipeline_id     = oci_devops_build_pipeline.this.id
    secret_ids            = var.secret_ids
  }
}

variable "registry_host" {
  type        = string
  default     = ""
  description = "Verified regional OCIR hostname, from the rollout inventory."
}
resource "oci_devops_deploy_artifact" "image" {
  count                      = var.registry_host == "" ? 0 : 1
  project_id                 = oci_devops_project.this.id
  display_name               = "${var.prefix}-image"
  deploy_artifact_type       = "DOCKER_IMAGE"
  argument_substitution_mode = "SUBSTITUTE_PLACEHOLDERS"
  deploy_artifact_source {
    deploy_artifact_source_type = "OCIR"
    image_uri                   = "${var.registry_host}/${data.oci_objectstorage_namespace.this.namespace}/${var.prefix}:$${IMAGE_TAG}"
  }
}
resource "oci_devops_build_pipeline_stage" "deliver" {
  count                     = var.registry_host == "" ? 0 : 1
  build_pipeline_id         = oci_devops_build_pipeline.this.id
  build_pipeline_stage_type = "DELIVER_ARTIFACT"
  display_name              = "Deliver reviewed immutable image"
  description               = "Publish a unique source commit tag to the private registry"
  build_pipeline_stage_predecessor_collection {
    items { id = oci_devops_build_pipeline_stage.build.id }
  }
  deliver_artifact_collection {
    items {
      artifact_id   = oci_devops_deploy_artifact.image[0].id
      artifact_name = "portfolio-image"
    }
  }
}

resource "oci_core_network_security_group_security_rule" "dns_tcp" {
  for_each                  = local.subnets
  network_security_group_id = oci_core_network_security_group.this[each.key].id
  direction                 = "EGRESS"
  protocol                  = "6"
  destination               = "169.254.169.254/32"
  tcp_options {
    destination_port_range {
      min = 53
      max = 53
    }
  }
}
resource "oci_logging_log_group" "this" {
  compartment_id = local.application_compartment_id
  display_name   = "${var.prefix}-operations"
}
resource "oci_logging_log" "build" {
  display_name = "${var.prefix}-build"
  log_group_id = oci_logging_log_group.this.id
  log_type     = "SERVICE"
  is_enabled   = true
  configuration {
    compartment_id = local.application_compartment_id
    source {
      source_type = "OCISERVICE"
      service     = "devops"
      category    = "all"
      resource    = oci_devops_project.this.id
    }
  }
}

locals {
  hosted_application_ids = [for id in var.runtime_ids : id if startswith(id, "ocid1.generativeaihostedapplication.")]
}
resource "oci_logging_log" "hosted_runtime" {
  count              = length(local.hosted_application_ids) > 0 ? 1 : 0
  display_name       = "${var.prefix}-runtime"
  log_group_id       = oci_logging_log_group.this.id
  log_type           = "SERVICE"
  is_enabled         = true
  retention_duration = 30
  freeform_tags      = local.tags
  configuration {
    compartment_id = local.application_compartment_id
    source {
      source_type = "OCISERVICE"
      service     = "genai-hosted-deployment-prod"
      resource    = local.hosted_application_ids[0]
      category    = "genai-hosted-deployment-log"
    }
  }
}
