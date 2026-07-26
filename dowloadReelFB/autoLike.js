(async function () {
    const MAX_LIKES = 50;
    const MIN_DELAY = 2500;
    const MAX_DELAY = 5000;

    console.log("%c[Hệ thống] Đang quét giao diện và mở rộng bình luận...", "color:#00a2ff");

    // 1. SỬA LỖI KHÔNG KÉO XUỐNG ĐƯỢC (Dùng ScrollIntoView thay vì window.scroll)
    for (let i = 0; i < 8; i++) {
        // Tìm tất cả các nút "Thích" hiện có
        const currentLikes = Array.from(document.querySelectorAll('div[role="button"]'))
            .filter(btn => btn.innerText.trim() === "Thích" || btn.innerText.trim() === "Like");

        // Kéo mượt mà đến nút Thích cuối cùng để kích hoạt tải thêm bình luận
        if (currentLikes.length > 0) {
            currentLikes[currentLikes.length - 1].scrollIntoView({ behavior: "smooth", block: "end" });
        }
        await new Promise(r => setTimeout(r, 1500));

        // Bấm các nút "Xem thêm bình luận" hoặc "X phản hồi"
        const moreBtns = Array.from(document.querySelectorAll('div[role="button"], span[role="button"]'))
            .filter(el => /(Xem thêm|View more|phản hồi|replies|tất cả.*bình luận)/i.test(el.innerText.trim()));

        if (moreBtns.length === 0 && i > 2) { // Quét ít nhất 3 vòng
            console.log(`%c[Hệ thống] Tạm dừng mở rộng ở vòng thứ ${i + 1}`, "color:orange");
            break;
        }

        moreBtns.forEach(el => {
            try { el.click(); } catch (e) { }
        });
        await new Promise(r => setTimeout(r, 1500));
    }

    // 2. SỬA LỖI LẤY TÊN TÁC GIẢ (Tối ưu cho Reels + Fallback chắc chắn)
    let authorName = null;
    const authorSelectors = [
        'h2[dir="auto"] span a span', // Reel thông thường
        'div[data-pagelet="Reels"] h2 span',
        'a[href*="/reel/"] strong',
        'h2 a[href*="facebook.com"]',
        'strong a[href*="facebook.com"]'
    ];

    for (let selector of authorSelectors) {
        const el = document.querySelector(selector);
        if (el && el.innerText.trim().length > 0) {
            authorName = el.innerText.trim();
            break;
        }
    }

    // Nếu Facebook lại đổi code và không quét được, hiển thị hộp thoại để bạn tự nhập
    if (!authorName) {
        authorName = prompt("Hệ thống bị ẩn tên tác giả. Vui lòng nhập tên tác giả (VD: Hoàng Khanh) để tool không like bình luận của họ:", "");
    }
    console.log(`%c[Info] Tác giả bài viết: ${authorName || "Không xác định"}`, "color:#aaa");

    const allLikeBtns = Array.from(document.querySelectorAll('div[role="button"]'))
        .filter(btn => btn.innerText.trim() === "Thích" || btn.innerText.trim() === "Like");

    console.log(`%c[Info] Quét được ${allLikeBtns.length} nút Like trên màn hình.`, "color:magenta;font-weight:bold");

    var delay = 0, count = 0, skipped = 0, alreadyLiked = 0, logData = [];

    // 3. SỬA LỖI LIKE ĐÈ (Nhận diện trạng thái Like chính xác hơn)
    for (const btn of allLikeBtns) {
        if (count >= MAX_LIKES) break;

        // Kiểm tra đã like chưa dựa trên thuộc tính HOẶC màu sắc (màu xanh Facebook)
        const isAriaPressed = btn.getAttribute('aria-pressed') === 'true' || btn.closest('[aria-pressed="true"]') !== null;
        const isBlueColor = window.getComputedStyle(btn).color === 'rgb(8, 102, 255)' || window.getComputedStyle(btn).color === 'rgb(24, 119, 242)';

        if (isAriaPressed || isBlueColor) {
            alreadyLiked++;
            continue;
        }

        // Lấy tên người Comment
        let commenterName = null, profileLink = null;
        let node = btn.parentElement;
        for (let i = 0; i < 8; i++) {
            if (!node) break;
            const links = node.querySelectorAll('a[href*="facebook.com/"], a[role="link"]');
            for (const a of links) {
                const name = a.innerText.trim();
                if (name.length < 2 || /^\d+\s*(giờ|phút|ngày|tuần|tháng|năm|hr|min|day|week|mon|ago)/i.test(name) || /^https?:\/\//.test(name)) continue;
                commenterName = name;
                profileLink = a.href.split('?')[0];
                break;
            }
            if (commenterName) break;
            node = node.parentElement;
        }

        if (authorName && commenterName && commenterName.toLowerCase().includes(authorName.toLowerCase())) {
            skipped++;
            logData.push({ STT: "-", "Tác Giả": authorName, "Tên": commenterName, "Trạng thái": "⏭ Bỏ qua (Tác giả)", "Thời gian thực": new Date().toLocaleString("vi-VN") });
            continue;
        }

        count++;
        var wait = Math.floor(Math.random() * (MAX_DELAY - MIN_DELAY)) + MIN_DELAY;
        delay += wait;
        logData.push({ STT: count, "Tác Giả": authorName || "?", "Tên": commenterName || "?", "Trạng thái": "✅ Đã Like", "Thời gian thực": new Date().toLocaleString("vi-VN") });

        (function (n, d, name, buttonEl) {
            setTimeout(() => {
                buttonEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
                // Kiểm tra chéo lần cuối trước khi click
                if (window.getComputedStyle(buttonEl).color !== 'rgb(8, 102, 255)') {
                    buttonEl.click();
                    console.log(`%c[Like #${n}] ${name || "?"} — Delay: ${(d / 1000).toFixed(1)}s`, "color:#0f0;font-weight:bold");
                }
            }, d);
        })(count, delay, commenterName, btn);
    }

    // BÁO CÁO KẾT QUẢ
    setTimeout(() => {
        console.log("%c=== BÁO CÁO KẾT QUẢ AUTO LIKE ===", "color:#00a2ff;font-weight:bold;font-size:14px");
        if (logData.length > 0) console.table(logData);
        console.log(`%c✔ Đã Like: ${count} | ⏭ Bỏ qua tác giả: ${skipped} | ✋ Đã like từ trước: ${alreadyLiked}`, "color:orange;font-weight:bold");

        if (logData.length > 0) {
            const headers = Object.keys(logData[0]).join("\t");
            const rows = logData.map(r => Object.values(r).join("\t")).join("\n");
            const csv = headers + "\n" + rows;

            const copyBtn = Object.assign(document.createElement("button"), {
                innerText: "📋 Copy Báo Cáo",
                style: `position:fixed;bottom:30px;right:30px;z-index:99999;background:#1877f2;color:white;border:none;border-radius:8px;padding:12px 20px;font-size:15px;font-weight:bold;cursor:pointer;box-shadow:0 4px 12px rgba(0,0,0,0.3)`
            });
            copyBtn.onclick = () => {
                navigator.clipboard.writeText(csv).then(() => {
                    copyBtn.innerText = "✅ Đã cCopy!";
                    setTimeout(() => copyBtn.innerText = "📋 Copy Báo Cáo", 2000);
                });
            };
            document.body.appendChild(copyBtn);
            setTimeout(() => copyBtn.remove(), 300000);
        }
    }, delay + 1000);
})();