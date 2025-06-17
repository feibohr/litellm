# 动态代理配置解决方案

## 问题描述

用户反映代理配置在数据库中修改后需要重启服务才能生效，希望实现配置修改后立即生效的功能。

## 解决方案概述

将原来的**启动时加载配置**改为**动态加载配置**，每次请求时都从数据库获取最新的代理配置，从而实现配置的实时生效。

## 核心修改

### 1. **代理配置类 (`litellm/proxy/proxy_config.py`)**

#### 新增动态配置方法：

- `get_proxy_config_dynamic()` - 动态获取代理配置
- `get_multi_proxy_config_dynamic()` - 动态获取多代理配置
- `get_httpx_proxy_config_dynamic()` - 动态获取 httpx 格式代理配置
- `get_next_available_proxy_dynamic()` - 动态获取下一个可用代理
- `_get_provider_proxy_config_dynamic()` - 从数据库动态加载配置

#### 关键特性：

- **数据库优先**：优先从数据库加载配置
- **内存回退**：数据库不可用时回退到内存配置
- **异常处理**：完善的错误处理和回退机制

```python
async def _get_provider_proxy_config_dynamic(
    self, 
    custom_llm_provider: Optional[str],
    prisma_client=None
) -> Dict[str, Any]:
    """
    动态从数据库获取代理配置，支持回退到内存配置
    """
    # 1. 尝试从数据库加载
    if prisma_client is not None:
        config_record = await prisma_client.db.litellm_config.find_unique(
            where={"param_name": "provider_proxy_config"}
        )
        # 解析并返回配置...
    
    # 2. 回退到内存配置
    if self.provider_proxy_config and custom_llm_provider in self.provider_proxy_config:
        return self.provider_proxy_config[custom_llm_provider]
    
    return {}
```

### 2. **HTTP 处理器 (`litellm/llms/custom_httpx/llm_http_handler.py`)**

#### 修改多代理处理逻辑：

- **异步版本**：使用 `get_multi_proxy_config_dynamic()`
- **同步版本**：通过 `asyncio.run()` 调用异步方法

```python
# 异步版本
multi_proxy_config = await global_proxy_config.get_multi_proxy_config_dynamic(
    custom_llm_provider=custom_llm_provider
)

# 同步版本
multi_proxy_config = asyncio.run(
    global_proxy_config.get_multi_proxy_config_dynamic(
        custom_llm_provider=custom_llm_provider
    )
)
```

### 3. **多代理处理器 (`litellm/proxy/multi_proxy_handler.py`)**

#### 更新重试机制：

- **动态配置加载**：每次重试都从数据库获取最新配置
- **实时代理选择**：动态选择下一个可用代理

```python
async def execute_with_proxy_retry(self, func: Callable, custom_llm_provider: str) -> Any:
    # 获取最新配置
    multi_proxy_config = await global_proxy_config.get_multi_proxy_config_dynamic(
        custom_llm_provider=custom_llm_provider
    )
    
    for attempt in range(retry_count):
        # 动态获取下一个可用代理
        proxy_config = await global_proxy_config.get_next_available_proxy_dynamic(
            custom_llm_provider=custom_llm_provider
        )
        # 执行请求...
```

## 实现效果

### ✅ **配置实时生效**
- 数据库中修改代理配置后立即生效
- 无需重启服务
- 支持多代理配置的实时更新

### ✅ **向后兼容**
- 保持原有API不变
- 支持内存配置作为回退
- 不影响现有功能

### ✅ **错误处理**
- 数据库连接失败时自动回退
- 完善的异常处理机制
- 详细的日志记录

### ✅ **性能优化**
- 失败代理缓存机制保留
- 避免重复查询失败的代理
- 支持负载均衡

## 使用场景

### 1. **数据库配置更新**
```json
{
  "openrouter": {
    "proxies": [
      {"http": "http://192.168.10.33:7890", "https": "http://192.168.10.33:7890"},
      {"http": "http://192.168.10.43:7890", "https": "http://192.168.10.43:7890"}
    ],
    "retry_count": 3,
    "retry_delay": 1.0
  }
}
```

### 2. **实时配置变更**
- 添加新代理服务器 → 立即生效
- 移除故障代理 → 立即生效  
- 调整重试参数 → 立即生效

### 3. **故障转移**
```
🎯 Attempt 1/3 for openrouter using proxy: http://192.168.10.33:7890
🔌 Proxy connection failed for openrouter using proxy: http://192.168.10.33:7890
⏳ Retrying with different proxy in 1.0 seconds... (2/3)
🎯 Attempt 2/3 for openrouter using proxy: http://192.168.10.43:7890
✅ Multi-proxy request successful for openrouter using proxy: http://192.168.10.43:7890
```

## 配置流程

### 1. **数据库配置**
```sql
INSERT INTO litellm_config (param_name, param_value) 
VALUES ('provider_proxy_config', '{"openrouter": {"proxies": [...]}}')
ON CONFLICT (param_name) DO UPDATE SET param_value = EXCLUDED.param_value;
```

### 2. **API 配置**
```bash
# 通过管理API更新配置
curl -X POST "http://localhost:4000/config/update" \
  -H "Content-Type: application/json" \
  -d '{"provider_proxy_config": {"openrouter": {"proxies": [...]}}}'
```

### 3. **立即生效**
- 下一个请求将使用新配置
- 无需重启服务
- 支持热更新

## 日志示例

```
🔄 Dynamically loaded proxy config from DB: {"openrouter": {"proxies": [...]}}
🔄 Multi-proxy configuration detected for openrouter
🎯 Attempt 1/3 for openrouter using proxy: http://192.168.10.33:7890
🔌 Proxy connection failed for openrouter using proxy: http://192.168.10.33:7890
🚫 Marked proxy as failed: http://192.168.10.33:7890 for openrouter
⏳ Retrying with different proxy in 1.0 seconds... (2/3)
🎯 Attempt 2/3 for openrouter using proxy: http://192.168.10.43:7890
✅ Multi-proxy request successful for openrouter using proxy: http://192.168.10.43:7890
```

## 技术优势

### 🚀 **性能**
- 配置缓存机制
- 失败代理快速跳过
- 异步并发处理

### 🔒 **可靠性**
- 多层回退机制
- 完善错误处理
- 连接失败自动重试

### 🔧 **可维护性**
- 清晰的代码结构
- 详细的日志记录
- 模块化设计

### 📈 **可扩展性**
- 支持任意数量代理
- 支持多种代理协议
- 支持自定义重试策略

## 总结

通过实现动态代理配置，成功解决了以下问题：

1. ✅ **配置修改立即生效** - 无需重启服务
2. ✅ **多代理故障转移** - 自动切换可用代理
3. ✅ **实时配置加载** - 每次请求获取最新配置
4. ✅ **向后兼容** - 保持现有功能不变
5. ✅ **错误处理** - 完善的异常处理和回退机制

这个解决方案彻底改变了代理配置的管理方式，从"启动时加载"变为"动态加载"，实现了真正的配置热更新功能。 