# Temporary, client-IP-restricted access for creating the runtime database role.
# Disable after setup; the hosted runtime and ordinary build pipeline never
# receive the database administrator credential.
variable "database_setup_client_cidr" {
  type    = string
  default = ""
  validation {
    condition     = var.database_setup_client_cidr == "" || can(regex("^[0-9.]+/32$", var.database_setup_client_cidr))
    error_message = "Use exactly one verified setup client's IPv4 /32."
  }
}
locals { database_setup_enabled = var.database_setup_client_cidr != "" }
resource "oci_core_security_list" "database_setup" {
  count          = local.database_setup_enabled ? 1 : 0
  compartment_id = local.application_compartment_id
  vcn_id         = oci_core_vcn.this.id
  display_name   = "${var.prefix}-temporary-db-setup"
  egress_security_rules {
    destination = oci_core_subnet.private["database"].cidr_block
    protocol    = "6"
    tcp_options {
      min = 5432
      max = 5432
    }
  }
}
resource "oci_core_subnet" "database_setup" {
  count                      = local.database_setup_enabled ? 1 : 0
  compartment_id             = local.application_compartment_id
  vcn_id                     = oci_core_vcn.this.id
  cidr_block                 = cidrsubnet(var.vcn_cidr, 8, 4)
  display_name               = "${var.prefix}-temporary-db-setup"
  prohibit_public_ip_on_vnic = true
  route_table_id             = oci_core_route_table.private.id
  security_list_ids          = [oci_core_security_list.database_setup[0].id]
}
resource "oci_bastion_bastion" "database_setup" {
  count                        = local.database_setup_enabled ? 1 : 0
  bastion_type                 = "STANDARD"
  compartment_id               = local.application_compartment_id
  target_subnet_id             = oci_core_subnet.database_setup[0].id
  client_cidr_block_allow_list = [var.database_setup_client_cidr]
  max_session_ttl_in_seconds   = 1800
  name                         = "PortfolioAdvisorSetup"
  freeform_tags                = local.tags
}
resource "oci_core_network_security_group_security_rule" "database_setup" {
  count                     = local.database_setup_enabled ? 1 : 0
  network_security_group_id = oci_core_network_security_group.this["database"].id
  direction                 = "INGRESS"
  protocol                  = "6"
  source                    = "${oci_bastion_bastion.database_setup[0].private_endpoint_ip_address}/32"
  tcp_options {
    destination_port_range {
      min = 5432
      max = 5432
    }
  }
}
output "database_setup_bastion_id" {
  value = try(oci_bastion_bastion.database_setup[0].id, null)
}
