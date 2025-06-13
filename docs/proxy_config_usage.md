# LiteLLM Proxy Configuration Guide

This guide explains how to configure HTTP proxies in LiteLLM with the new two-tier priority system.

## Overview

LiteLLM supports proxy configuration at two levels with the following priority order:

1. **Model Level** (Highest Priority) - `litellm_params.proxy_config`
2. **Provider Level** (Lowest Priority) - Database stored provider-specific configs

## 1. Model Level Proxy Configuration

Configure proxies directly in your model's `litellm_params` for the highest priority proxy settings.

### Config YAML Format

```yaml
model_list:
  - model_name: gpt-4-proxy
    litellm_params:
      model: gpt-4
      api_key: os.environ/OPENAI_API_KEY
      # Model-specific proxy configuration
      proxy_config:
        http: "http://proxy.company.com:8080"
        https: "http://proxy.company.com:8080"
        no_proxy: "localhost,127.0.0.1"
```

### Programmatic Configuration

```python
from litellm.types.router import LiteLLM_Params

# Create model configuration with proxy
litellm_params = LiteLLM_Params(
    model="gpt-4",
    api_key="your-api-key",
    proxy_config={
        "http": "http://proxy.company.com:8080",
        "https": "http://proxy.company.com:8080",
        "no_proxy": "localhost,127.0.0.1"
    }
)
```

## 2. Provider Level Proxy Configuration

Provider-level configurations are stored in the database `LiteLLM_Config` table and apply to all models from specific providers unless overridden by model-level settings.

### Database Configuration

The proxy configuration is stored in the `LiteLLM_Config` table with:
- `param_name`: "provider_proxy_config"  
- `param_value`: JSON string containing provider proxy mappings

Example database entry:
```json
{
  "openai": {
    "http": "http://openai-proxy.company.com:8080",
    "https": "http://openai-proxy.company.com:8080"
  },
  "openrouter": {
    "http": "http://openrouter-proxy.company.com:8080",
    "https": "http://openrouter-proxy.company.com:8080"
  },
  "anthropic": {
    "http": "http://anthropic-proxy.company.com:8080", 
    "https": "http://anthropic-proxy.company.com:8080"
  },
  "azure*": {
    "http": "http://azure-proxy.company.com:8080",
    "https": "http://azure-proxy.company.com:8080"
  }
}
```

### Managing Provider Configurations

```python
from litellm.proxy.utils import update_provider_proxy_config

# Update provider proxy configuration
provider_config = {
    "openai": {
        "http": "http://openai-proxy.company.com:8080",
        "https": "http://openai-proxy.company.com:8080"
    },
    "openrouter": {
        "http": "http://openrouter-proxy.company.com:8080",
        "https": "http://openrouter-proxy.company.com:8080"
    },
    "anthropic": {
        "http": "http://anthropic-proxy.company.com:8080",
        "https": "http://anthropic-proxy.company.com:8080"
    }
}

# This will update both memory and database
await update_provider_proxy_config(provider_config, prisma_client)
```

## Proxy Configuration Format

### Supported Fields

- `http`: HTTP proxy URL (e.g., "http://proxy.company.com:8080")
- `https`: HTTPS proxy URL (e.g., "http://proxy.company.com:8080")
- `no_proxy`: Comma-separated list of hosts to bypass proxy (e.g., "localhost,127.0.0.1,*.internal.com")
- `all`: Apply same proxy to both HTTP and HTTPS

### URL Formats

The system automatically normalizes proxy URLs:

```python
# These are all valid formats:
"proxy.company.com:8080"           # → "http://proxy.company.com:8080"
"http://proxy.company.com:8080"    # → "http://proxy.company.com:8080"
"https://proxy.company.com:8080"   # → "https://proxy.company.com:8080"
"socks5://proxy.company.com:1080"  # → "socks5://proxy.company.com:1080"
```

## Priority System Examples

### Example 1: Model Override

```yaml
# config.yaml
model_list:
  - model_name: gpt-4-special
    litellm_params:
      model: gpt-4
      custom_llm_provider: openai
      # This model-level config overrides any provider-level config
      proxy_config:
        http: "http://special-proxy.company.com:8080"
        https: "http://special-proxy.company.com:8080"
```

Database provider config:
```json
{
  "openai": {
    "http": "http://default-openai-proxy.company.com:8080"
  }
}
```

**Result**: The model `gpt-4-special` will use `special-proxy.company.com:8080`, while other OpenAI models use `default-openai-proxy.company.com:8080`.

### Example 2: Provider Patterns

Provider configurations support wildcard patterns:

```json
{
  "azure*": {
    "http": "http://azure-proxy.company.com:8080"
  },
  "openai": {
    "http": "http://openai-proxy.company.com:8080"
  },
  "*": {
    "http": "http://default-proxy.company.com:8080"
  }
}
```

- `azure/gpt-4` → uses `azure-proxy.company.com:8080`
- `azure/gpt-3.5-turbo` → uses `azure-proxy.company.com:8080`  
- `openai/gpt-4` → uses `openai-proxy.company.com:8080`
- `anthropic/claude-3` → uses `default-proxy.company.com:8080`

## Testing Your Configuration

Use the built-in testing utilities to verify your proxy setup:

```python
from litellm.proxy.proxy_config import global_proxy_config
from litellm.types.router import LiteLLM_Params

# Test model-level proxy
model_params = LiteLLM_Params(
    model="gpt-4",
    proxy_config={"http": "http://test-proxy.com:8080"}
)

proxy_config = global_proxy_config.get_httpx_proxy_config(
    litellm_params=model_params,
    custom_llm_provider="openai"
)

print(f"Proxy configuration: {proxy_config}")
# Output: {'http://': 'http://test-proxy.com:8080'}
```

## Best Practices

1. **Use Model-Level for Exceptions**: Configure model-level proxies only for special cases that need different routing
2. **Provider-Level for Standards**: Use provider-level configurations for consistent proxy routing across providers
3. **Database Management**: Always use the provided utility functions to update configurations to ensure database consistency
4. **Testing**: Test proxy configurations in non-production environments first
5. **Monitoring**: Monitor proxy usage and performance to ensure proper routing 