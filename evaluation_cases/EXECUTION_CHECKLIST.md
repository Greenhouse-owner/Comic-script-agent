# Comic Agent 评测执行检查清单

## 📋 总览

- [ ] Case 01: 灯塔女孩与发光星鱼
- [ ] Case 05: 失眠少女与收梦猫
- [ ] Case 03: 旧教学楼的黑板留言
- [ ] Case 02: 学校操场的外星信号
- [ ] Case 04: 地下机甲比赛
- [ ] 生成最终评测报告

**预计总时间**: 2-3 小时

---

## 🎯 Case 01: 灯塔女孩与发光星鱼

### 步骤 1: 启动 Comic Agent
```bash
cd "/Users/a1/Documents/GitHub/lab 13/Comic-script-agent"
python3 main.py
```

### 步骤 2: 输入故事种子
```
我想创作一个儿童奇幻漫画，主角是一个住在海边灯塔里的女孩米娅。她发现海里有会发光的星鱼，这些星鱼能打开通往云上图书馆的路。故事要温暖、神秘、适合 8-12 岁孩子阅读。第一章写米娅第一次发现星鱼，并意识到灯塔隐藏着秘密。
```

### 步骤 3: 等待 Architect 完成
- [ ] 观察终端输出
- [ ] 确认角色卡生成完成
- [ ] 确认场景卡生成完成
- [ ] 确认剧本生成完成
- **预计时间**: 1-2 分钟

### 步骤 4: 生成分镜
```
/director
```

### 步骤 5: 等待 Director 完成
- [ ] 观察终端输出
- [ ] 确认分镜生成完成
- **预计时间**: 1-2 分钟

### 步骤 6: 输入 Revision 请求
```
把主角第一次发现发光星鱼的分镜改得更震撼，给一个大格，但不要改变剧本事实，也不要提前展示云上图书馆。
```

### 步骤 7: 等待 Revision 完成
- [ ] 观察终端输出
- [ ] 确认修改完成
- [ ] 确认 QA 检查通过
- **预计时间**: 1-2 分钟

### 步骤 8: 退出并收集输出
```
Ctrl+C 或输入 /exit
```

然后在新终端执行：
```bash
cd "/Users/a1/Documents/GitHub/lab 13/Comic-script-agent"
python3 collect_case_output.py case_01
```

### 步骤 9: 验证输出文件
- [ ] 检查 `evaluation_cases/case_01_lighthouse/comic_agent_output/` 目录
- [ ] 确认包含角色卡、场景卡、剧本、分镜、QA 报告

---

## 🎯 Case 05: 失眠少女与收梦猫

### 步骤 1: 启动 Comic Agent
```bash
cd "/Users/a1/Documents/GitHub/lab 13/Comic-script-agent"
python3 main.py
```

### 步骤 2: 输入故事种子
```
我想做一个治愈日常漫画。主角晚晚是一个经常失眠的少女，她在深夜遇到一只会收集梦境碎片的猫。猫不会直接说话，只会用尾巴画出发光的图案。第一章写晚晚第一次遇见这只猫，并感到自己没有那么孤单。
```

### 步骤 3-9: [同 Case 01]

**Revision 请求**:
```
把少女和猫第一次对视的情绪写得更安静、更治愈，不要让猫开口说话，也不要变成搞笑风格。
```

**收集输出**:
```bash
python3 collect_case_output.py case_05
```

---

## 🎯 Case 03: 旧教学楼的黑板留言

### 步骤 1: 启动 Comic Agent
```bash
cd "/Users/a1/Documents/GitHub/lab 13/Comic-script-agent"
python3 main.py
```

### 步骤 2: 输入故事种子
```
我想做一个校园悬疑漫画。主角许然是校报社成员，她在旧教学楼里发现一块黑板每天都会出现新的留言，内容似乎预告第二天发生的小事故。第一章写她第一次发现留言，并开始怀疑这不是恶作剧。整体要悬疑但不恐怖。
```

### 步骤 3-9: [同 Case 01]

**Revision 请求**:
```
增强旧教学楼黑板留言的悬疑感，但不要提前揭示留言来源，也不要把故事改成恐怖风格。
```

**收集输出**:
```bash
python3 collect_case_output.py case_03
```

---

## 🎯 Case 02: 学校操场的外星信号

### 步骤 1: 启动 Comic Agent
```bash
cd "/Users/a1/Documents/GitHub/lab 13/Comic-script-agent"
python3 main.py
```

### 步骤 2: 输入故事种子
```
我想写一个少年科幻漫画。主角林小北是一个普通中学生，他在学校操场捡到一个会发出脉冲光的金属圆片。当天晚上，操场中央出现一道发光裂缝，似乎连接着一艘外星飞船。故事要有紧张感，但第一章不要让外星人正式登场，只让主角发现异常。
```

### 步骤 3-9: [同 Case 01]

**Revision 请求**:
```
把外星信号第一次出现的场景改得更紧张，但不要让外星人提前登场，也不要让主角突然知道飞船真相。
```

**收集输出**:
```bash
python3 collect_case_output.py case_02
```

---

## 🎯 Case 04: 地下机甲比赛

### 步骤 1: 启动 Comic Agent
```bash
cd "/Users/a1/Documents/GitHub/lab 13/Comic-script-agent"
python3 main.py
```

### 步骤 2: 输入故事种子
```
我想创作一个热血少年漫画。主角阿泽是维修铺学徒，偷偷报名参加地下机甲比赛。他的机甲很旧，但他很熟悉每一个零件。第一章写他第一次进入地下赛场，看到强大的对手，并决定驾驶旧机甲上场。
```

### 步骤 3-9: [同 Case 01]

**Revision 请求**:
```
把机甲比赛开始前的压迫感加强，增加一个主角握紧操纵杆的特写，但不要把旧机甲改成高级机甲。
```

**收集输出**:
```bash
python3 collect_case_output.py case_04
```

---

## 📊 最终步骤：生成评测报告

### 1. 检查所有输出文件
```bash
cd "/Users/a1/Documents/GitHub/lab 13/Comic-script-agent"
ls -la evaluation_cases/*/comic_agent_output/
```

### 2. 人工评分
- 打开 `evaluation_cases/EVALUATION_REPORT_TEMPLATE.md`
- 对比每个案例的 Revision 前后变化
- 按照 5 个维度评分（1-5 分）

### 3. 填写评测报告
- 记录每个案例的评分和关键发现
- 计算平均分和总分
- 撰写综合分析和结论

---

## 💡 提示

1. **每个案例之间建议休息 2-3 分钟**，避免疲劳影响判断
2. **保存每个案例的终端输出**，可以用于报告中的案例分析
3. **如果某个案例出错**，记录错误信息并继续下一个案例
4. **Revision 请求可以复制粘贴**，确保输入准确
5. **观察 QA 报告**，它会指出修改是否符合预期

---

## 🚀 开始执行

准备好后，从 Case 01 开始执行。祝测试顺利！
