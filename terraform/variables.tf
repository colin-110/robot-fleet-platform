variable "aws_region" {
  description = "AWS region for deployment"
  default     = "us-east-1"
}

variable "project_name" {
  description = "Prefix for all resources"
  default     = "robot-fleet"
}

variable "db_username" {
  description = "PostgreSQL Master Username"
  default     = "fleetadmin"
}

variable "db_password" {
  description = "PostgreSQL Master Password (must be at least 8 characters)"
  type        = string
  sensitive   = true
}

variable "ec2_instance_type" {
  description = "EC2 instance type (t3.micro is Free Tier eligible)"
  default     = "t3.micro"
}

variable "rds_instance_class" {
  description = "RDS instance class (db.t3.micro is Free Tier eligible)"
  default     = "db.t3.micro"
}

variable "redis_node_type" {
  description = "ElastiCache node type (cache.t3.micro is Free Tier eligible)"
  default     = "cache.t3.micro"
}
