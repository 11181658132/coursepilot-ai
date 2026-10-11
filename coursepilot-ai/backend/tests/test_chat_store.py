import tempfile
import unittest
from pathlib import Path

from app.models import ChatResponse, Citation
from app.services.chat_store import ChatStore


class ChatStoreTests(unittest.TestCase):
    def test_saves_lists_and_rates_conversation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ChatStore(Path(directory) / "chat.db")
            answer = ChatResponse(
                answer="KNN uses nearest neighbours.",
                response_id="resp_1",
                citations=[Citation(filename="03-knn.pdf")],
                latency_ms=123,
                estimated_cost_usd=0.0001,
            )
            conversation_id, message_id = store.save_exchange(
                None, "90049", "lecture", "How does KNN work?", answer
            )

            conversations = store.list_conversations()
            self.assertEqual(len(conversations), 1)
            self.assertEqual(conversations[0].message_count, 2)
            messages = store.get_messages(conversation_id)
            self.assertEqual([message.role for message in messages], ["user", "assistant"])
            self.assertEqual(messages[1].citations[0].filename, "03-knn.pdf")

            feedback = store.set_feedback(message_id, "helpful", None)
            self.assertEqual(feedback.rating, "helpful")
            self.assertEqual(store.get_messages(conversation_id)[1].feedback, "helpful")

    def test_rejects_feedback_for_missing_message(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ChatStore(Path(directory) / "chat.db")
            with self.assertRaises(ValueError):
                store.set_feedback("missing", "unhelpful", "not grounded")


if __name__ == "__main__":
    unittest.main()
