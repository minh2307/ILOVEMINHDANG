({includeReplies, domAttribute, scopeAttribute}) => {
                const visible = (el) => {
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return rect.width > 0 && rect.height > 0 &&
                        style.display !== 'none' && style.visibility !== 'hidden';
                };
                const commentLike = (el) => {
                    const label = el.getAttribute('aria-label') || '';
                    if (/comment by|bình luận của/i.test(label) ||
                        el.matches('[data-commentid], [data-comment-id]')) {
                        return true;
                    }
                    return [...el.querySelectorAll(
                        'a[href*="comment_id="], a[href*="reply_comment_id="]'
                    )].some((link) =>
                        link.closest('[role="article"]') === el
                    );
                };
                const root = [...document.querySelectorAll(`[${scopeAttribute}]`)]
                    .find(visible);
                if (!root) return [];
                let candidates = [...root.querySelectorAll('[role="article"]')]
                    .filter((el) => visible(el) && commentLike(el));
                if (!candidates.length) {
                    candidates = [...root.querySelectorAll(
                        '[data-commentid], [data-comment-id]'
                    )].filter(visible);
                }
                let sequence = 0;
                return candidates.map((container) => {
                    const hrefs = [...container.querySelectorAll('a[href]')];
                    const permalink = hrefs.map((a) => a.href || a.getAttribute('href') || '')
                        .find((href) => /(?:comment_id|reply_comment_id)=/i.test(href)) || '';
                    const ancestorArticle = container.parentElement?.closest(
                        '[role="article"], [data-commentid], [data-comment-id]'
                    );
                    const nested = !!ancestorArticle && commentLike(ancestorArticle);
                    const ariaLevel = Number(container.getAttribute('aria-level') || '1');
                    const isReply = nested || ariaLevel > 1 || /reply_comment_id=/i.test(permalink);
                    if (isReply && !includeReplies) return null;

                    const profile = hrefs.find((link) => {
                        const href = link.getAttribute('href') || '';
                        const text = (link.innerText || link.getAttribute('aria-label') || '').trim();
                        return text && /(facebook\.com|^\/)/i.test(href) &&
                            !/(?:comment_id|reply_comment_id)=|\/(reel|watch|videos)\//i.test(href) &&
                            !/^\d+[smhdw]|^\d+\s*(phút|giờ|ngày|tuần)/i.test(text) &&
                            !/^(like|thích|reply|phản hồi|share|chia sẻ)$/i.test(text);
                    }) || null;
                    const authorName = profile ?
                        (profile.innerText || profile.getAttribute('aria-label') || '').trim() : '';
                    const messageNodes = [
                        ...container.querySelectorAll(
                            '[data-ad-preview="message"], '
                            '[data-ad-comet-preview="message"], '
                            'span[dir="auto"], div[dir="auto"]'
                        )
                    ].filter((node) => {
                        if (!visible(node) || node.closest('a') || node.querySelector('[dir="auto"]')) return false;
                        const text = (node.innerText || '').trim();
                        return text && text !== authorName &&
                            !/^(like|thích|reply|phản hồi|share|chia sẻ|see more|xem thêm)$/i.test(text) &&
                            !/^\d+[smhdw]|^\d+\s*(phút|giờ|ngày|tuần)/i.test(text);
                    });
                    const text = messageNodes.length ? (messageNodes[0].innerText || '').trim() : '';
                    let commentId = container.getAttribute('data-commentid') ||
                        container.getAttribute('data-comment-id') || null;
                    if (!commentId && permalink) {
                        const match = permalink.match(/[?&](?:comment_id|reply_comment_id)=([^&#]+)/i);
                        commentId = match ? decodeURIComponent(match[1]) : null;
                    }
                    if (!commentId) {
                        const dataFt = container.getAttribute('data-ft') || '';
                        const match = dataFt.match(/"(?:comment_id|reply_comment_id)"\s*:\s*"?(\d+)/i);
                        commentId = match ? match[1] : null;
                    }
                    let domKey = container.getAttribute(domAttribute);
                    if (!domKey) {
                        sequence += 1;
                        domKey = `comment-${Date.now()}-${sequence}`;
                        container.setAttribute(domAttribute, domKey);
                    }
                    const hovercard = profile ? (
                        profile.getAttribute('data-hovercard') ||
                        profile.getAttribute('data-hovercard-prefer-more-content-show') || ''
                    ) : '';
                    const actorMatch = hovercard.match(/[?&](?:id|profile_id)=(\d+)/i) ||
                        (profile ? profile.href.match(/\/people\/[^/]+\/(\d+)/i) : null);
                    return {
                        dom_key: domKey,
                        comment_id: commentId,
                        text,
                        is_reply: isReply,
                        author: {
                            name: authorName,
                            id: actorMatch ? actorMatch[1] : null,
                            url: profile ? (profile.href || profile.getAttribute('href')) : null,
                        },
                    };
                }).filter(Boolean);
            }
