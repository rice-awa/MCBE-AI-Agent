import { initializeAddonEarly, initializeAddonAfterWorldLoad } from "./bootstrap";

// 早期执行阶段：仅注册事件订阅，不访问 world 状态
initializeAddonEarly();

// 延迟初始化：订阅 worldLoad，在世界就绪后执行需要 world 状态的操作
initializeAddonAfterWorldLoad();
