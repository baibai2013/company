#!/bin/bash
# Create separate databases for each service
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" <<-EOSQL
    CREATE DATABASE gitea;
    CREATE DATABASE mattermost;
    CREATE DATABASE n8n;
    CREATE DATABASE company_app;
    CREATE DATABASE company_langgraph;
EOSQL

# 在 company_app 启用 pgvector 扩展（新部署自动配置）
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "company_app" <<-EOSQL
    CREATE EXTENSION IF NOT EXISTS vector;
EOSQL
