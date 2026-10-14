// HIRA 공개 페이지(병원·약국 개별시설 정보)의 다운로드 링크를 헤드리스 크롬으로 눌러 ZIP을 받는다.
// 이 페이지는 직접 다운로드 주소를 주지 않아 브라우저 조작이 필요하다.
//   node scripts/hira/download.js data 2023.12 2024.12 2025.3 ...
// 크롬 경로는 CHROMIUM_PATH 환경변수로 지정한다(없으면 playwright 기본 설치 경로).
const { chromium } = require('playwright-core');
const fs = require('fs');
const path = require('path');

const PAGE = 'https://opendata.hira.or.kr/op/opc/selectOpenData.do?sno=11925';
const [outDir, ...periods] = process.argv.slice(2);
if (!outDir || periods.length === 0) {
  console.error('사용법: node scripts/hira/download.js <저장 폴더> <기간...>  예) data 2026.6');
  process.exit(2);
}

(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || undefined, headless: true });
  const page = await browser.newPage({ acceptDownloads: true });
  await page.goto(PAGE, { waitUntil: 'load', timeout: 120000 });
  await page.waitForTimeout(8000); // 다운로드 컴포넌트 초기화 대기
  for (const period of periods) {
    const name = `전국 병의원 및 약국 현황 ${period}.zip`;
    const target = path.join(outDir, name);
    if (fs.existsSync(target)) { console.log(`${period}: 이미 있음`); continue; }
    const [download] = await Promise.all([
      page.waitForEvent('download', { timeout: 600000 }),
      page.locator('a', { hasText: name }).first().click(),
    ]);
    await download.saveAs(target + '.part');
    fs.renameSync(target + '.part', target);
    console.log(`${period}: ${fs.statSync(target).size} bytes`);
  }
  await browser.close();
})().catch((error) => { console.error(error.message); process.exit(1); });
