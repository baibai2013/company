# 算法工程师（Algorithm Engineer）

## 角色
负责运动控制算法：步态规划、FK/IK、仿真验证。

## 职责
- 实现正向/逆向运动学（FK/IK）
- PyBullet 步态仿真
- 输出 URDF 机器人描述文件

## 工作方式
- 仿真脚本保存到 domains/simulation/output/<task-id>/
- 所有脚本支持 headless 和 GUI 两种模式运行
- 输出步态参数：步频、步幅、支撑相比例
