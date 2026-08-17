(el) => {
                        const article = el.closest('[role="article"]');
                        if (!article) return false;
                        const label = article.getAttribute('aria-label') || '';
                        if (/comment by|bình luận của/i.test(label) ||
                            article.matches('[data-commentid], [data-comment-id]')) {
                            return true;
                        }
                        return [...article.querySelectorAll(
                            'a[href*="comment_id="], a[href*="reply_comment_id="]'
                        )].some((link) =>
                            link.closest('[role="article"]') === article
                        );
                    }
