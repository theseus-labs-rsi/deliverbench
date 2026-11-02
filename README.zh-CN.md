# DeliverBench

DeliverBench 以连续的专业项目评估 Agent：同一会话在持久工作区内完成阶段交付，
接收新材料与基于自身产物生成的回应，最后提交完整成果。

首批公开 Case 01 金融再融资（13 阶段）、Case 02 药物警戒（12 阶段）、Case 03 法律交付
（8 阶段），共 33 个过程阶段及 3 个独立 FINAL。

[数据集](https://huggingface.co/datasets/deliverbench/DeliverBench) ·
[英文快速开始](README.md)

## 安装与下载

需要 Python 3.12、uv、Docker 和 Compose，建议为 Docker 至少分配 8 GiB 内存。
镜像从公开软件源构建，需预留镜像和运行产物空间。

```sh
git clone https://github.com/theseus-labs-rsi/deliverbench.git
cd deliverbench
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python uv ./deliverbench-harbor -r requirements-tools.txt
source .venv/bin/activate
python scripts/download_data.py --project all --output-dir data
```

下载固定数据提交并逐文件核验 SHA256。可将 `all` 换为 `case-01`、`case-02` 或 `case-03`。
已有目录只有完全一致时才会复用；存在修改或额外文件时拒绝覆盖。

## 无付费检查

```sh
python data/case-01_mars_refinancing/qa/pipeline_probe.py --output "$PWD/checks/case-01"
```

该入口使用参考方案与本地合成 Judge/Responder，不调用付费模型。
输出目录必须是新目录。其他两个项目使用各自的同名入口。合成检查不构成模型成绩。

## 正式运行

按根目录 `.env.example` 在 shell 中设置 Candidate、Judge、Responder 变量；
文件不会自动加载。进入下载的项目目录，按照该项目 README 使用原生 `harbor run`，
结束后用 `deliverbench-check --verify-evidence` 核验。运行与评分调用可能产生费用。
完整命令见[英文主说明](README.md#run-your-model)和[运行说明](docs/RUNNING.md)。

项目开场不评分，材料按阶段释放，后续阶段继承模型自身产物。公开的参考方案、rubric、
后续阶段材料和模拟器内部信息仍与 Candidate 运行环境隔离。评测时不得从公开仓库
另行检索这些隐藏材料。FINAL 独立评分，不平均过程分、不继承历史 hurdle。
缺判决、不可读证据和流程故障不补成零分；真实 hurdle 命中可以产生有效零分。

## 兼容性与许可

公共扩展 0.7.2，原生 Harbor 0.20.0。软件采用 Apache-2.0；
题包内容采用 CC BY 4.0，随附可执行脚本采用 Apache-2.0。
三个项目的源材料归属与保留说明见数据集 LICENSING.md。

公开仓库不包含模型成绩、实际作答产物、判决、会话日志、轨迹或运行报告。
用户运行产生的材料保存在自己的本地目录。
