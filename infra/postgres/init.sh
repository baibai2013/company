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
