provider "aws" {
  region = var.aws_region
}

data "aws_availability_zones" "available" {}

# ==========================================
# 1. NETWORKING (VPC, Subnets, Internet Gateway)
# ==========================================
resource "aws_vpc" "main" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = { Name = "${var.project_name}-vpc" }
}

resource "aws_internet_gateway" "igw" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "${var.project_name}-igw" }
}

resource "aws_subnet" "public_a" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = "10.0.1.0/24"
  availability_zone       = data.aws_availability_zones.available.names[0]
  map_public_ip_on_launch = true
  tags                    = { Name = "${var.project_name}-public-a" }
}

resource "aws_subnet" "public_b" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = "10.0.2.0/24"
  availability_zone       = data.aws_availability_zones.available.names[1]
  map_public_ip_on_launch = true
  tags                    = { Name = "${var.project_name}-public-b" }
}

resource "aws_subnet" "private_a" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = "10.0.3.0/24"
  availability_zone       = data.aws_availability_zones.available.names[0]
  map_public_ip_on_launch = false
  tags                    = { Name = "${var.project_name}-private-a" }
}

resource "aws_subnet" "private_b" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = "10.0.4.0/24"
  availability_zone       = data.aws_availability_zones.available.names[1]
  map_public_ip_on_launch = false
  tags                    = { Name = "${var.project_name}-private-b" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.igw.id
  }
  tags = { Name = "${var.project_name}-public-rt" }
}

resource "aws_route_table_association" "pub_a" {
  subnet_id      = aws_subnet.public_a.id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table_association" "pub_b" {
  subnet_id      = aws_subnet.public_b.id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table" "private" {
  vpc_id = aws_vpc.main.id
  tags = { Name = "${var.project_name}-private-rt" }
}

resource "aws_route_table_association" "priv_a" {
  subnet_id      = aws_subnet.private_a.id
  route_table_id = aws_route_table.private.id
}

resource "aws_route_table_association" "priv_b" {
  subnet_id      = aws_subnet.private_b.id
  route_table_id = aws_route_table.private.id
}

# ==========================================
# 2. SECURITY GROUPS
# ==========================================
resource "aws_security_group" "alb_sg" {
  name        = "${var.project_name}-alb-sg"
  description = "Allow inbound HTTP/HTTPS traffic to ALB"
  vpc_id      = aws_vpc.main.id

  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "app_sg" {
  name        = "${var.project_name}-app-sg"
  description = "Allow traffic from ALB to EC2 instances"
  vpc_id      = aws_vpc.main.id

  ingress {
    from_port       = 80
    to_port         = 80
    protocol        = "tcp"
    security_groups = [aws_security_group.alb_sg.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "db_sg" {
  name        = "${var.project_name}-db-sg"
  description = "Allow DB traffic from EC2"
  vpc_id      = aws_vpc.main.id

  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.app_sg.id]
  }
}

resource "aws_security_group" "redis_sg" {
  name        = "${var.project_name}-redis-sg"
  description = "Allow Redis traffic from EC2"
  vpc_id      = aws_vpc.main.id

  ingress {
    from_port       = 6379
    to_port         = 6379
    protocol        = "tcp"
    security_groups = [aws_security_group.app_sg.id]
  }
}

# ==========================================
# 3. APPLICATION LOAD BALANCER (ALB)
# ==========================================
resource "aws_lb" "app_alb" {
  name               = "${var.project_name}-alb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb_sg.id]
  subnets            = [aws_subnet.public_a.id, aws_subnet.public_b.id]
}

resource "aws_lb_target_group" "app_tg" {
  name     = "${var.project_name}-tg"
  port     = 80
  protocol = "HTTP"
  vpc_id   = aws_vpc.main.id

  health_check {
    path                = "/api/v1/health" # Assuming healthcheck exists
    protocol            = "HTTP"
    interval            = 30
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 2
  }
}

resource "aws_lb_listener" "http_listener" {
  load_balancer_arn = aws_lb.app_alb.arn
  port              = "80"
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.app_tg.arn
  }
}

# ==========================================
# 4. MANAGED DATABASE (RDS Free Tier)
# ==========================================
resource "aws_db_subnet_group" "db_subnet" {
  name       = "${var.project_name}-db-subnet-group"
  subnet_ids = [aws_subnet.private_a.id, aws_subnet.private_b.id]
}

resource "aws_db_instance" "postgres" {
  identifier             = "${var.project_name}-db"
  engine                 = "postgres"
  engine_version         = "15"
  instance_class         = var.rds_instance_class
  allocated_storage      = 20
  db_name                = "fleetops"
  username               = var.db_username
  password               = var.db_password
  db_subnet_group_name   = aws_db_subnet_group.db_subnet.name
  vpc_security_group_ids = [aws_security_group.db_sg.id]
  
  skip_final_snapshot    = true
  publicly_accessible    = false
  multi_az               = false # Important: Keep false for Free Tier!
}

# ==========================================
# 5. MANAGED CACHE (ElastiCache Redis Free Tier)
# ==========================================
resource "aws_elasticache_subnet_group" "redis_subnet" {
  name       = "${var.project_name}-redis-subnet-group"
  subnet_ids = [aws_subnet.private_a.id, aws_subnet.private_b.id]
}

resource "aws_elasticache_cluster" "redis" {
  cluster_id           = "${var.project_name}-redis"
  engine               = "redis"
  node_type            = var.redis_node_type
  num_cache_nodes      = 1
  parameter_group_name = "default.redis7"
  engine_version       = "7.1"
  port                 = 6379
  subnet_group_name    = aws_elasticache_subnet_group.redis_subnet.name
  security_group_ids   = [aws_security_group.redis_sg.id]
}

# ==========================================
# 6. AUTO SCALING GROUP (EC2 Free Tier)
# ==========================================
data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*"]
  }
}

resource "aws_launch_template" "app_lt" {
  name_prefix   = "${var.project_name}-lt"
  image_id      = data.aws_ami.ubuntu.id
  instance_type = var.ec2_instance_type

  network_interfaces {
    security_groups = [aws_security_group.app_sg.id]
    associate_public_ip_address = true
  }

  user_data = base64encode(<<-EOF
              #!/bin/bash
              apt-get update -y
              apt-get install -y docker.io docker-compose-v2
              systemctl start docker
              systemctl enable docker
              
              # Here we would normally pull the code from Git or S3 and run docker-compose.
              # In a real deployment pipeline, AWS CodeDeploy or a similar tool would trigger the code pull.
              EOF
  )
}

resource "aws_autoscaling_group" "app_asg" {
  name                = "${var.project_name}-asg"
  vpc_zone_identifier = [aws_subnet.public_a.id, aws_subnet.public_b.id]
  target_group_arns   = [aws_lb_target_group.app_tg.arn]

  # To guarantee this stays in the AWS Free Tier, we restrict min/max to exactly 1.
  # (750 hours of t3.micro per month = exactly 1 instance running 24/7).
  desired_capacity = 1
  min_size         = 1
  max_size         = 1

  launch_template {
    id      = aws_launch_template.app_lt.id
    version = "$Latest"
  }
}

# ==========================================
# OUTPUTS
# ==========================================
output "alb_dns_name" {
  description = "The DNS name of the Load Balancer (Frontend URL)"
  value       = aws_lb.app_alb.dns_name
}

output "rds_endpoint" {
  description = "PostgreSQL Database Connection Endpoint"
  value       = aws_db_instance.postgres.endpoint
}

output "redis_endpoint" {
  description = "ElastiCache Redis Connection Endpoint"
  value       = aws_elasticache_cluster.redis.cache_nodes[0].address
}
