### V1 note: HTTP only (no ACM/HTTPS listener yet) — TLS termination is addressed
### as part of V17 (Security). Documented as a trade-off in ADR-002.
###
### V7 (Microservices): path-based routing to Product / Order / Payment target
### groups — same shape as the local nginx gateway. See ADR-008.

resource "aws_lb" "this" {
  name               = "${var.name}-alb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [var.security_group_id]
  subnets            = var.public_subnet_ids

  access_logs {
    bucket  = var.access_logs_bucket
    enabled = true
  }

  tags = merge(var.tags, { Name = "${var.name}-alb" })
}

resource "aws_lb_target_group" "product" {
  name        = "${var.name}-product-tg"
  port        = var.container_port
  protocol    = "HTTP"
  vpc_id      = var.vpc_id
  target_type = "ip"

  health_check {
    path                = var.health_check_path
    protocol            = "HTTP"
    matcher             = "200"
    interval            = 15
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }

  tags = merge(var.tags, { Name = "${var.name}-product-tg" })
}

resource "aws_lb_target_group" "order" {
  name        = "${var.name}-order-tg"
  port        = var.container_port
  protocol    = "HTTP"
  vpc_id      = var.vpc_id
  target_type = "ip"

  health_check {
    path                = var.health_check_path
    protocol            = "HTTP"
    matcher             = "200"
    interval            = 15
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }

  tags = merge(var.tags, { Name = "${var.name}-order-tg" })
}

resource "aws_lb_target_group" "payment" {
  name        = "${var.name}-payment-tg"
  port        = var.container_port
  protocol    = "HTTP"
  vpc_id      = var.vpc_id
  target_type = "ip"

  health_check {
    path                = var.health_check_path
    protocol            = "HTTP"
    matcher             = "200"
    interval            = 15
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }

  tags = merge(var.tags, { Name = "${var.name}-payment-tg" })
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.this.arn
  port              = 80
  protocol          = "HTTP"

  # Default → Order (customers + orders + unmatched paths).
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.order.arn
  }
}

resource "aws_lb_listener_rule" "products" {
  listener_arn = aws_lb_listener.http.arn
  priority     = 10

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.product.arn
  }

  condition {
    path_pattern {
      values = ["/products", "/products/*", "/internal/*"]
    }
  }
}

resource "aws_lb_listener_rule" "payments" {
  listener_arn = aws_lb_listener.http.arn
  priority     = 20

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.payment.arn
  }

  condition {
    path_pattern {
      values = ["/payments", "/payments/*"]
    }
  }
}
