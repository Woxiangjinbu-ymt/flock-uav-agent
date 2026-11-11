# 2B 学生推理

学生使用 [Qwen3.5-2B 固定修订](../sft/model.json)。下载或从缓存提供该修订之后，先核对权重和分词文件。

```sh
python3 -m posttraining.inference.weights /path/to/Qwen3.5-2B
```

`--download` 可在目标机获取清单中的官方文件。模型权重由外部缓存持有。

| 运行环境 | 实现与设置 |
| --- | --- |
| Mac Apple Silicon | [metal.MacModel](metal.py) 在独立 MLX 进程加载 BF16 基座。构造函数接收模型环境的 Python 和权重目录，`generate(ids, limit, seed, deadline)` 返回原始生成 ID 和耗时。[Mac 环境锁](requirements-macos-metal.lock) |
| Linux 单卡 CUDA | [cuda.TransformersModel](cuda.py) 核对本地权重后在 BF16 GPU 上执行文本生成。`generate(ids, limit, seed, eos)` 返回生成 ID、首 token 时刻和显存统计。[A100 CUDA 12.8 环境锁](requirements-linux-cu128.lock) |

两种后端使用相同的抽样配置。调用方用[学生适配器](../sft/adapter.py)的 `render` 构造提示，使用 `parse_generation` 验证完整回复、终止标记和工具参数后再派发动作。输出长度上限 2,048 token，含提示的上下文上限 32,768 token。
