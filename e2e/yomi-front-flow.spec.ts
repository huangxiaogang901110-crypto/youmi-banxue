import { test, expect } from '@playwright/test';

test.describe('悠米前端 E2E 主链路', () => {

  test('工作台上传 → 识别 → 清单 → 切题 → 分步/完整解析 → 缓存回归', async ({ page }) => {

    // ── 0. 清除旧登录状态 ──
    await page.goto('/');
    await page.evaluate(() => localStorage.clear());

    // ════════════════════════════════════════
    // 0b. UI 登录
    // ════════════════════════════════════════
    await page.goto('/login/');
    await page.waitForLoadState('domcontentloaded');

    // 切换到密码登录 tab
    await page.getByText('密码登录').click();

    // 填手机号
    const phoneInput = page.locator('input[type="tel"]');
    await phoneInput.fill('13900001111');

    // 填密码
    const pwdInput = page.locator('input[type="password"]');
    await pwdInput.fill('123456');

    // 勾选协议 — checkbox 是 sr-only，点 label
    await page.getByText('我已阅读并同意').click();

    // 提交
    await page.getByRole('button', { name: '登录 / 进入工作台' }).click();

    // 等待登录完成 → 跳转到首页
    await page.waitForURL((url) => !url.pathname.includes('login'), { timeout: 15_000 });
    await page.waitForLoadState('domcontentloaded');

    // Route 拦截：公网 IP → localhost（安全兜底）
    await page.route('**/39.107.119.136:8001/**', (route) => {
      route.continue({ url: route.request().url().replace('39.107.119.136:8001', 'localhost:8001') });
    });

    // ════════════════════════════════════════
    // 1. 打开工作台页面
    // ════════════════════════════════════════
    await page.goto('/workspace/');
    await page.waitForLoadState('domcontentloaded');

    // 页面不能白屏 — 上传区域存在
    await expect(page.getByText('拍整页作业')).toBeVisible({ timeout: 10_000 });

    // ════════════════════════════════════════
    // 2. 上传测试图片
    // ════════════════════════════════════════
    const fileInput = page.locator('input[type="file"][accept*="image"]').first();
    await fileInput.setInputFiles('e2e/fixtures/sample.png');

    // 等待「开始识别」按钮出现
    await expect(page.getByText('开始识别')).toBeVisible({ timeout: 5_000 });
    await page.getByText('开始识别').click();

    // ════════════════════════════════════════
    // 3. 等待识别完成
    // ════════════════════════════════════════
    try {
      await page.waitForSelector('text=/共\\s*\\d+\\s*题/', { timeout: 120_000 });
    } catch {
      await page.screenshot({ path: 'test-results/fail-parse_timeout.png', fullPage: true });
      const body = await page.locator('body').textContent();
      throw new Error(`识别超时 120s。页面文本: ${body?.slice(0, 300)}`);
    }

    // 验证有题目（「完整解析」按钮）
    await expect(page.getByText('完整解析').first()).toBeVisible({ timeout: 10_000 });

    // 保存当前 job URL（后续直接导航回来，绕过 SwipeableCard touch-only 问题）
    const completedJobUrl = page.url();

    // ════════════════════════════════════════
    // 4. 识别作业清单 — 点「新上传」回到列表
    // ════════════════════════════════════════
    await page.getByText('新上传').click();
    await page.waitForLoadState('domcontentloaded');

    // 确认历史记录
    await expect(page.getByText('历史记录（最近 7 天）')).toBeVisible({ timeout: 5_000 });
    await expect(page.locator('text=/\\d+\\s*题/').first()).toBeVisible({ timeout: 5_000 });

    // ════════════════════════════════════════
    // 4b. 刷新后清单仍存在
    // ════════════════════════════════════════
    await page.reload();
    await page.waitForLoadState('domcontentloaded');
    await expect(page.getByText('历史记录（最近 7 天）')).toBeVisible({ timeout: 10_000 });

    // ════════════════════════════════════════
    // 5. 切题 — 直接导航回已完成的 job URL
    //    SwipeableCard 只绑 touch 事件，桌面 Chromium click 不触发 onTap
    // ════════════════════════════════════════
    await page.goto(completedJobUrl);
    await page.waitForLoadState('domcontentloaded');

    // 等待题目列表
    await expect(page.getByText('完整解析').first()).toBeVisible({ timeout: 15_000 });

    // 点第一题「完整解析」→ 进入 /question 页面
    await page.getByText('完整解析').first().click();
    await page.waitForURL(/\/question\/?\?qid=/, { timeout: 10_000 });

    // 确认题目页
    await expect(page.getByText('查看完整解析').or(page.getByText('给我一点提示')).first())
      .toBeVisible({ timeout: 10_000 });

    // ════════════════════════════════════════
    // 6. 分步解析
    // ════════════════════════════════════════
    await page.getByText('给我一点提示').click();

    // 等待 typewriter 内容出现
    try {
      await page.waitForFunction(() => {
        const els = Array.from(document.querySelectorAll('.rounded-2xl.rounded-tl-md'));
        for (const el of els) {
          if ((el.textContent?.trim().length || 0) > 20) return true;
        }
        return false;
      }, { timeout: 60_000 });
    } catch {
      await page.screenshot({ path: 'test-results/fail-hint_timeout.png', fullPage: true });
      throw new Error('分步解析超时 60s');
    }

    const hintEl = page.locator('.rounded-2xl.rounded-tl-md').first();
    const hintContent = await hintEl.textContent() || '';

    // ════════════════════════════════════════
    // 7. 完整解析
    // ════════════════════════════════════════
    await page.getByText('查看完整解析').click();

    try {
      await page.waitForFunction(() => {
        const els = Array.from(document.querySelectorAll('.rounded-2xl.rounded-tl-md'));
        const last = els[els.length - 1];
        return last && (last.textContent?.trim().length || 0) > 20;
      }, { timeout: 60_000 });
    } catch {
      await page.screenshot({ path: 'test-results/fail-solve_timeout.png', fullPage: true });
      throw new Error('完整解析超时 60s');
    }

    const solveEl = page.locator('.rounded-2xl.rounded-tl-md').first();
    const solveContent = await solveEl.textContent() || '';
    expect(solveContent.trim().length).toBeGreaterThan(5);

    // ════════════════════════════════════════
    // 8. 缓存/状态回归
    // ════════════════════════════════════════
    await page.reload();
    await page.waitForLoadState('domcontentloaded');

    // 按钮仍可见
    await expect(
      page.getByText('给我一点提示').or(page.getByText('查看完整解析')).first()
    ).toBeVisible({ timeout: 10_000 });

    // 再点完整解析
    await page.getByText('查看完整解析').click();

    try {
      await page.waitForFunction(() => {
        const els = Array.from(document.querySelectorAll('.rounded-2xl.rounded-tl-md'));
        const last = els[els.length - 1];
        return last && (last.textContent?.trim().length || 0) > 20;
      }, { timeout: 60_000 });
    } catch {
      await page.screenshot({ path: 'test-results/fail-cache_timeout.png', fullPage: true });
      throw new Error('缓存回归：完整解析超时 60s');
    }

    const cacheSolveEl = page.locator('.rounded-2xl.rounded-tl-md').first();
    const cacheSolveContent = await cacheSolveEl.textContent() || '';

    // 完整解析 ≠ 分步解析旧内容
    if (hintContent.trim() && cacheSolveContent.trim()) {
      expect(hintContent).not.toBe(cacheSolveContent);
    }
  });
});
