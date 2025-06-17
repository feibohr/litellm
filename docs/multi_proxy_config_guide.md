# LiteLLM 多代理配置指南

LiteLLM现在支持为单个供应商配置多个代理，当某个代理连接失败时自动切换到备用代理。

## 功能特性

- ✅ **供应商级别多代理配置**：为单个供应商配置多个VPN/代理端口
- ✅ **自动故障转移**：代理连接失败时自动切换到下一个可用代理  
- ✅ **智能重试机制**：可配置重试次数和延迟时间
- ✅ **失败缓存**：临时缓存失败的代理，避免重复尝试
- ✅ **负载均衡**：随机选择可用代理，分散流量
- ✅ **兼容现有配置**：完全向后兼容单代理配置

## 配置格式

### 1. 多代理配置格式

在数据库的 `LiteLLM_Config` 表中，`param_name` 为 `"provider_proxy_config"`，`param_value` 配置如下：

```json
{
  "openrouter": {
    "proxies": [
      {
        "http": "http://192.168.10.43:7890",
        "https": "http://192.168.10.43:7890"
      },
      {
        "http": "http://192.168.10.43:7891", 
        "https": "http://192.168.10.43:7891"
      },
      {
        "http": "http://192.168.10.43:7892",
        "https": "http://192.168.10.43:7892"
      }
    ],
    "retry_count": 3,
    "retry_delay": 1.0
  },
  "anthropic": {
    "proxies": [
      {
        "http": "http://192.168.10.44:7890",
        "https": "http://192.168.10.44:7890" 
      },
      {
        "http": "http://192.168.10.44:7891",
        "https": "http://192.168.10.44:7891"
      }
    ],
    "retry_count": 2,
    "retry_delay": 0.5
  }
}
```

### 2. 单代理配置格式（仍然支持）

```json
{
  "openrouter": {
    "http": "http://192.168.10.43:7890",
    "https": "http://192.168.10.43:7890"
  }
}
```

## 配置参数说明

| 参数 | 类型 | 必需 | 默认值 | 说明 |
|------|------|------|--------|------|
| `proxies` | Array | 是 | - | 代理列表，每个代理包含 http/https 配置 |
| `retry_count` | Integer | 否 | 3 | 最大重试次数（不包括初始尝试） |
| `retry_delay` | Float | 否 | 1.0 | 重试间隔时间（秒） |

## 工作原理

### 1. 代理选择流程

1. **初始选择**：系统随机选择一个可用代理
2. **连接测试**：尝试使用选中的代理建立连接
3. **失败处理**：如果连接失败，将该代理添加到临时失败列表
4. **自动切换**：选择下一个未失败的代理重试
5. **重置机制**：所有代理都失败时，清空失败列表重新开始

### 2. 故障检测

系统检测以下类型的连接错误并触发代理切换：
- `httpx.ProxyError` - 代理服务器错误
- `httpx.ConnectError` - 连接错误
- `httpx.TimeoutException` - 超时错误
- `ConnectionError` - 通用连接错误

### 3. 缓存机制

- **失败缓存**：临时记录失败的代理，避免短时间内重复尝试
- **成功重置**：连接成功时清空该供应商的失败缓存
- **智能恢复**：所有代理都失败时自动重置缓存

## 使用示例

### 示例 1：基本多代理配置

为 OpenRouter 配置3个VPN代理：

```python
import asyncio
from litellm.proxy.utils import update_provider_proxy_config

# 多代理配置
provider_config = {
    "openrouter": {
        "proxies": [
            {"http": "http://192.168.10.43:7890", "https": "http://192.168.10.43:7890"},
            {"http": "http://192.168.10.43:7891", "https": "http://192.168.10.43:7891"},
            {"http": "http://192.168.10.43:7892", "https": "http://192.168.10.43:7892"}
        ],
        "retry_count": 3,
        "retry_delay": 1.0
    }
}

# 更新配置到数据库
async def update_config():
    await update_provider_proxy_config(provider_config, prisma_client)

# 运行配置更新
asyncio.run(update_config())
```

### 示例 2：测试多代理配置

```python
import litellm

# 测试调用
response = litellm.completion(
    model="openrouter/anthropic/claude-3-haiku",
    messages=[{"role": "user", "content": "Hello!"}],
    custom_llm_provider="openrouter"
)

print(response.choices[0].message.content)
```

### 示例 3：混合配置

同时配置多代理和单代理供应商：

```python
provider_config = {
    # 多代理配置 - OpenRouter
    "openrouter": {
        "proxies": [
            {"http": "http://192.168.10.43:7890", "https": "http://192.168.10.43:7890"},
            {"http": "http://192.168.10.43:7891", "https": "http://192.168.10.43:7891"}
        ],
        "retry_count": 2,
        "retry_delay": 1.0
    },
    
    # 单代理配置 - OpenAI
    "openai": {
        "http": "http://192.168.10.44:7890",
        "https": "http://192.168.10.44:7890"
    },
    
    # 通配符配置 - Azure
    "azure*": {
        "http": "http://192.168.10.45:7890",
        "https": "http://192.168.10.45:7890"
    }
}
```

## 日志监控

系统提供详细的日志记录，帮助监控代理使用情况：

```
🌐 [MULTI-PROXY] Creating Async HTTP client for openrouter with proxy: {'http': 'http://192.168.10.43:7890', 'https': 'http://192.168.10.43:7890'}
🌐 [MULTI-PROXY] Attempt 1/4 for openrouter using proxy: {'http': 'http://192.168.10.43:7890', 'https': 'http://192.168.10.43:7890'}
⚠️  [WARNING] Proxy connection failed for openrouter (attempt 1/4): ProxyError("Proxy connection failed")
🌐 [MULTI-PROXY] Attempt 2/4 for openrouter using proxy: {'http': 'http://192.168.10.43:7891', 'https': 'http://192.168.10.43:7891'}
✅ [SUCCESS] Successfully executed request for openrouter using proxy: {'http': 'http://192.168.10.43:7891', 'https': 'http://192.168.10.43:7891'}
```

## 错误处理

### 1. 所有代理失败

当所有配置的代理都失败时，系统会抛出 `ProxyConnectionError`：

```python
try:
    response = litellm.completion(
        model="openrouter/anthropic/claude-3-haiku",
        messages=[{"role": "user", "content": "Hello!"}],
        custom_llm_provider="openrouter"
    )
except Exception as e:
    if "All proxy connections failed" in str(e):
        print("所有代理连接都失败了，请检查网络配置")
    else:
        print(f"其他错误: {e}")
```

### 2. 配置验证

系统会自动验证代理配置格式：

```python
# 错误的配置格式会被拒绝
invalid_config = {
    "openrouter": {
        "proxies": "invalid_format"  # 应该是数组
    }
}

# 正确的配置格式
valid_config = {
    "openrouter": {
        "proxies": [
            {"http": "http://proxy1:port"},
            {"https": "https://proxy2:port"}
        ]
    }
}
```

## 最佳实践

### 1. 代理数量

- **建议配置 2-4 个代理**：太少可能不够冗余，太多会增加管理复杂性
- **确保代理稳定性**：选择稳定可靠的代理服务器

### 2. 重试配置

- **retry_count**: 建议设置为 2-3，避免过度重试
- **retry_delay**: 建议设置为 0.5-2.0 秒，平衡响应速度和服务器压力

### 3. 监控和告警

- **启用日志记录**：监控代理使用情况和失败率
- **设置告警**：当代理失败率超过阈值时及时告警
- **定期检查**：定期检查代理服务器状态

### 4. 网络安全

- **使用认证代理**：为代理服务器配置用户名密码认证
- **加密传输**：优先使用 HTTPS 代理
- **访问控制**：限制代理服务器的访问源IP

## 故障排除

### 常见问题

1. **配置不生效**
   - 检查数据库中的配置是否正确保存
   - 重启 LiteLLM 代理服务器重新加载配置

2. **代理连接失败**
   - 检查代理服务器是否正常运行
   - 验证代理地址和端口是否正确
   - 确认网络连通性

3. **性能问题**
   - 适当调整 retry_delay 减少重试延迟
   - 检查代理服务器性能和带宽
   - 考虑减少 retry_count 避免过度重试

### 调试技巧

1. **启用详细日志**：
   ```python
   import litellm
   litellm.set_verbose = True
   ```

2. **测试单个代理**：
   ```python
   # 临时使用单代理配置测试
   test_config = {
       "openrouter": {
           "http": "http://192.168.10.43:7890",
           "https": "http://192.168.10.43:7890"
       }
   }
   ```

3. **检查代理状态**：
   ```bash
   # 测试代理连通性
   curl -x http://192.168.10.43:7890 https://api.openrouter.ai/api/v1/models
   ```

## 更新日志

- **v1.0.0**: 初始版本，支持多代理配置和自动故障转移
- **v1.0.1**: 优化代理选择算法，增加负载均衡功能
- **v1.0.2**: 改进错误处理和日志记录功能

## 技术支持

如果遇到问题或需要技术支持，请：

1. 查看详细日志输出
2. 检查网络连接和代理服务器状态  
3. 验证配置格式是否正确
4. 尝试使用单代理配置测试

---

*本指南基于 LiteLLM 多代理扩展功能编写，适用于需要代理故障自动切换的企业环境。* 